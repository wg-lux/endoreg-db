"""
Video streaming views.

Canonical binary endpoints:
- /api/media/videos/<pk>/stream/
- /api/media/videos/<pk>/ (legacy alias)
"""

from __future__ import annotations

import logging
import mimetypes
import os
import posixpath
from pathlib import Path

from django.http import Http404, HttpResponse
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import VideoFile
from endoreg_db.models.media.video.storage_mode import (
    VideoStorageMode,
    coerce_video_storage_mode,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.paths import STORAGE_DIR

from ..media.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    field_file_size,
    parse_byte_range,
)

logger = logging.getLogger(__name__)
NGINX_PROTECTED_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


def _pick_video_field_file(video: VideoFile, file_type: str):
    if file_type == "processed":
        field_file = getattr(video, "processed_file", None)
        if not field_file or not getattr(field_file, "name", None):
            raise Http404("No processed video file available for this entry")
        return field_file

    try:
        field_file = video.active_raw_file
    except Exception as exc:
        raise Http404("No raw video file available for this entry") from exc
    if not getattr(field_file, "name", None):
        raise Http404("No raw video file available for this entry")
    return field_file


def _normalize_internal_relative_path(relative_path: str) -> str | None:
    candidate = Path(relative_path)
    if str(relative_path or "").strip() == "":
        return None
    if candidate.is_absolute():
        return None
    if any(part in {"..", ""} for part in candidate.parts):
        return None
    normalized = Path(*candidate.parts).as_posix()
    if normalized in {"", "."}:
        return None
    return normalized


def _streamable_relative_path(
    video: VideoFile, file_type: str, field_file
) -> str | None:
    explicit_relative_path = (
        getattr(video, "processed_streamable_relative_path", "")
        if file_type == "processed"
        else getattr(video, "streamable_relative_path", "")
    )
    normalized_explicit = _normalize_internal_relative_path(explicit_relative_path)
    if normalized_explicit is not None:
        return normalized_explicit

    # Backward-compatible fallback for rows where streamable path fields were not
    # populated yet, but the file field already points to a streamable-safe path.
    fallback_field_name = str(getattr(field_file, "name", "") or "")
    return _normalize_internal_relative_path(fallback_field_name)


def _stream_not_ready_reason(
    video: VideoFile, file_type: str, field_file
) -> str | None:
    try:
        storage_mode = coerce_video_storage_mode(getattr(video, "storage_mode", None))
    except ValueError:
        return "unsupported_storage_mode"

    if storage_mode != VideoStorageMode.STREAMABLE:
        return None

    relative_path = _streamable_relative_path(video, file_type, field_file)
    if relative_path is None:
        return "missing_streamable_path"

    expected_path = (Path(STORAGE_DIR) / relative_path).resolve()
    if not expected_path.exists():
        return "missing_streamable_artifact"

    return None


def _serve_streamable_video_with_nginx(
    video: VideoFile,
    *,
    file_type: str,
    field_file,
    content_type: str,
) -> HttpResponse | None:
    try:
        storage_mode = coerce_video_storage_mode(getattr(video, "storage_mode", None))
    except ValueError:
        logger.warning(
            "Video %s has unsupported storage_mode=%r. Falling back to Django streaming.",
            getattr(video, "pk", "unknown"),
            getattr(video, "storage_mode", None),
        )
        return None

    match storage_mode:
        case VideoStorageMode.ENCRYPTED:
            return None
        case VideoStorageMode.STREAMABLE:
            pass

    relative_path = _streamable_relative_path(video, file_type, field_file)
    if relative_path is None:
        logger.warning(
            "Video %s is marked fs_encrypted_streamable but has no safe relative path for %s.",
            getattr(video, "pk", "unknown"),
            file_type,
        )
        return None

    response = HttpResponse()
    response["Content-Type"] = content_type
    response["X-Accel-Redirect"] = posixpath.join(
        NGINX_PROTECTED_URL.rstrip("/"), relative_path
    )
    response["X-Accel-Buffering"] = "no"
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'inline; filename="{Path(field_file.name).name}"'
    return response


class VideoStreamView(APIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request, pk=None):
        if pk is None:
            raise Http404("Video ID is required")

        try:
            video_id = int(pk)
        except (TypeError, ValueError):
            raise Http404("Invalid video ID format")

        try:
            video = VideoFile.objects.get(pk=video_id)
        except VideoFile.DoesNotExist as exc:
            raise Http404(f"Video with ID {pk} not found") from exc

        file_type = str(
            request.query_params.get("type")
            or request.query_params.get("file_type")
            or "raw"
        ).lower()
        if file_type not in {"raw", "processed"}:
            file_type = "raw"

        field_file = _pick_video_field_file(video, file_type)
        filename = Path(field_file.name).name
        content_type = mimetypes.guess_type(field_file.name)[0] or "video/mp4"
        file_size = field_file_size(field_file)
        if file_size <= 0:
            raise Http404("Video file is empty")

        stream_not_ready_reason = _stream_not_ready_reason(video, file_type, field_file)
        if stream_not_ready_reason is not None:
            response = HttpResponse(
                "Video stream is not ready",
                status=409,
                content_type="text/plain",
            )
            response["X-Stream-State"] = stream_not_ready_reason
            return add_cors_headers(
                response,
                os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000"),
            )

        frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")
        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")
        serve_with_nginx = os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"

        if serve_with_nginx:
            nginx_response = _serve_streamable_video_with_nginx(
                video,
                file_type=file_type,
                field_file=field_file,
                content_type=content_type,
            )
            if nginx_response is not None:
                return add_cors_headers(nginx_response, frontend_origin)

        if range_header:
            try:
                parse_byte_range(range_header, file_size)
            except ValueError:
                response = HttpResponse(status=416, content_type=content_type)
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return add_cors_headers(response, frontend_origin)

        response = build_partial_content_response(
            field_file=field_file,
            content_type=content_type,
            file_size=file_size,
            range_header=range_header,
            disposition="inline",
            filename=filename,
        )
        return add_cors_headers(response, frontend_origin)
