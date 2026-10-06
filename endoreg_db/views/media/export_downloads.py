from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import cast

from cryptography.exceptions import InvalidTag
from django.db import transaction
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.exceptions import MediaOperationDeferred
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.openapi import OpenApiAPIView
from endoreg_db.services.media.export_downloads import (
    annotation_csv,
    leased_video_bytes,
    timestamp_csv,
    validate_video_download,
)
from endoreg_db.services.media.operation_gate import (
    create_video_stream_lease,
)
from endoreg_db.utils.storage_streaming import (
    build_partial_content_response,
    field_file_size,
)
from endoreg_db.views.access_control import assert_center_scope_allowed
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


class VideoExportDownloadView(OpenApiAPIView):
    """Download accepted anonymized MP4 or CSV attachments.

    Annotation CSV uses persisted frame coordinates and accepts use_export_flags.
    Timestamp CSV accepts timeline=original or processed and exports independent
    canonical sequences. MP4 downloads support Range and content-hash If-Range.
    Negotiate application/json for errors; attachments declare their content type.
    """

    permission_classes = [IsAuthenticated, PolicyPermission]

    def get(
        self, request: Request, pk: int, kind: str
    ) -> StreamingHttpResponse | Response:
        allowed_options = {"timeline"} if kind == "timestamps" else {"use_export_flags"}
        if set(request.query_params) - allowed_options:
            return Response({"error": "Unbekannte Exportoption."}, status=400)
        flag = request.query_params.get("use_export_flags", "false")
        timeline = request.query_params.get("timeline", "processed")
        if timeline not in {"original", "processed"}:
            return Response({"error": "Unbekannte Zeitstempelfolge."}, status=400)
        if flag not in {"true", "false"}:
            return Response(
                {"error": "use_export_flags muss true oder false sein."}, status=400
            )
        try:
            with transaction.atomic():
                video = get_object_or_404(VideoFile.objects.select_for_update(), pk=pk)
                assert_center_scope_allowed(request=request, obj=video)
                self.check_object_permissions(request, video)
                create_video_stream_lease(video, file_type="processed")
                validate_video_download(video)
                if kind == "video":
                    size = field_file_size(video.processed_file)
                    etag = f'"{video.processed_video_hash}"'
                    range_header = request.headers.get("Range")
                    if request.headers.get("If-Range", etag) != etag:
                        range_header = None
                    try:
                        response = build_partial_content_response(
                            field_file=video.processed_file,
                            content_type="video/mp4",
                            file_size=size,
                            range_header=range_header,
                            disposition="attachment",
                            filename=f"video_{pk}_anonymisiert.mp4",
                        )
                    except ValueError:
                        error = Response(
                            {"error": "Ungültiger Bytebereich."}, status=416
                        )
                        error["Content-Range"] = f"bytes */{size}"
                        return error
                    response["ETag"] = etag
                    response.streaming_content = leased_video_bytes(
                        video, cast(Iterable[bytes], response.streaming_content)
                    )
                else:
                    lines = (
                        timestamp_csv(video, original=timeline == "original")
                        if kind == "timestamps"
                        else annotation_csv(video, use_export_flags=flag == "true")
                    )
                    response = StreamingHttpResponse(
                        lines,
                        content_type="text/csv; charset=utf-8",
                    )
                    response["Content-Disposition"] = (
                        f'attachment; filename="video_{pk}_annotationen.csv"'
                    )
                    if kind == "timestamps":
                        response["Content-Disposition"] = (
                            f'attachment; filename="video_{pk}_{timeline}_pts.csv"'
                        )
        except MediaOperationDeferred:
            return Response(
                {"error": "Das Video wird gerade bearbeitet. Bitte erneut versuchen."},
                status=409,
            )
        except (ValueError, OSError, InvalidTag, RuntimeError) as exc:
            # The native authenticated-decryption reader raises RuntimeError.
            emit_structured_event(
                logger,
                "video_download_rejected",
                level=logging.WARNING,
                video_id=pk,
                export_kind=kind,
                reason=type(exc).__name__,
            )
            return Response(
                {
                    "error": "Export nicht verfügbar: Validierung, Datei, Integrität oder vollständige Präsentationszeitstempel fehlen."
                },
                status=409,
            )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
