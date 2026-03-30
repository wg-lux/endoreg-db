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
from pathlib import Path

from django.http import Http404, HttpResponse
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.paths import STORAGE_DIR

from ..media.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    field_file_size,
    maybe_local_plaintext_path,
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


def _serve_with_nginx(field_file, content_type: str) -> HttpResponse | None:
    path = maybe_local_plaintext_path(field_file)
    if path is None:
        return None

    try:
        relative_path = path.relative_to(STORAGE_DIR.resolve())
    except ValueError:
        logger.warning(
            "File %s is not inside STORAGE_DIR %s. Falling back to Django streaming.",
            path,
            STORAGE_DIR,
        )
        return None

    response = HttpResponse()
    response["Content-Type"] = content_type
    response["X-Accel-Redirect"] = os.path.join(NGINX_PROTECTED_URL, str(relative_path))
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

        frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")
        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")
        serve_with_nginx = os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"

        if serve_with_nginx and not range_header:
            nginx_response = _serve_with_nginx(field_file, content_type)
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
