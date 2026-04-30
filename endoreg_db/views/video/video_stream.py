"""
Video streaming views.

Canonical binary endpoints:
- /api/media/videos/<pk>/stream/
- /api/media/videos/<pk>/ (legacy alias)
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.http import Http404, HttpResponse
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import VideoFile
from endoreg_db.utils.cors import resolve_response_origin
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.nginx_accel import (
    build_nginx_accel_response,
    nginx_offload_enabled,
)
from endoreg_db.utils.paths import resolve_existing_protected_media_path
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    build_partial_content_response_from_path,
    field_file_size,
    parse_byte_range,
)

logger = logging.getLogger(__name__)


def _path_starts_with_encrypted_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
    except OSError:
        return False


class VideoStreamView(APIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _parse_file_type(request) -> str:
        file_type = str(
            request.query_params.get("type")
            or request.query_params.get("file_type")
            or "raw"
        ).lower()
        return file_type if file_type in {"raw", "processed"} else "processed"

    @staticmethod
    def _get_video_or_404(pk) -> VideoFile:
        if pk is None:
            raise Http404("Video ID is required")
        try:
            video_id = int(pk)
        except (TypeError, ValueError) as exc:
            raise Http404("Invalid video ID format") from exc

        try:
            return VideoFile.objects.get(pk=video_id)
        except VideoFile.DoesNotExist as exc:
            raise Http404(f"Video with ID {pk} not found") from exc

    def get(self, request, pk=None):
        video = self._get_video_or_404(pk)
        self.check_object_permissions(request, video)

        file_type = self._parse_file_type(request)
        frontend_origin = resolve_response_origin(request)
        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")

        stream_relative_path = None
        if video.can_offload_stream_with_nginx(file_type):
            stream_relative_path = video.get_stream_relative_path(file_type)
            if stream_relative_path is not None:
                resolved_stream_path = resolve_existing_protected_media_path(
                    stream_relative_path
                )
                if resolved_stream_path is None:
                    response = HttpResponse(status=409)
                    response["X-Stream-State"] = "missing_streamable_artifact"
                    return add_cors_headers(response, frontend_origin)
                if _path_starts_with_encrypted_magic(resolved_stream_path):
                    logger.error(
                        "Refusing nginx offload for encrypted streamable artifact: %s",
                        resolved_stream_path,
                    )
                    stream_relative_path = None

        if nginx_offload_enabled() and stream_relative_path is not None:
            relative_path = video.get_stream_relative_path(file_type)
            if relative_path is not None:
                content_type = mimetypes.guess_type(relative_path)[0] or "video/mp4"
                return build_nginx_accel_response(
                    protected_relative_path=relative_path,
                    content_type=content_type,
                    filename=Path(relative_path).name,
                    disposition="inline",
                    frontend_origin=frontend_origin,
                )

        try:
            field_file, local_path = video.resolve_video_stream_source(
                file_type,
                materialize_if_missing=False,
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.warning(
                "Video stream source unavailable for id=%s type=%s: %s",
                getattr(video, "pk", None),
                file_type,
                exc,
            )
            raise Http404("Video file is not available") from exc

        filename = (
            local_path.name if local_path is not None else Path(field_file.name).name
        )
        content_type = (
            mimetypes.guess_type(
                str(local_path if local_path is not None else field_file.name)
            )[0]
            or "video/mp4"
        )

        try:
            file_size = (
                local_path.stat().st_size
                if local_path is not None
                else field_file_size(field_file)
            )
        except FileNotFoundError as exc:
            logger.warning(
                "Video file disappeared during streaming setup for id=%s type=%s: %s",
                getattr(video, "pk", None),
                file_type,
                exc,
            )
            raise Http404("Video file is not available") from exc

        if file_size <= 0:
            raise Http404("Video file is empty")

        if range_header:
            try:
                parse_byte_range(range_header, file_size)
            except ValueError:
                response = HttpResponse(status=416, content_type=content_type)
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return add_cors_headers(response, frontend_origin)

        if local_path is not None:
            response = build_partial_content_response_from_path(
                file_path=local_path,
                content_type=content_type,
                file_size=file_size,
                range_header=range_header,
                disposition="inline",
                filename=filename,
            )
        else:
            response = build_partial_content_response(
                field_file=field_file,
                content_type=content_type,
                file_size=file_size,
                range_header=range_header,
                disposition="inline",
                filename=filename,
            )

        return add_cors_headers(response, frontend_origin)
