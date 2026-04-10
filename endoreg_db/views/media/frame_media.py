import logging
import mimetypes
import os
from pathlib import Path

from django.core.files import File
from django.http import Http404, HttpResponse
from rest_framework.views import APIView

from endoreg_db.models import Frame, VideoFile
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.paths import STORAGE_DIR, ensure_within_protected_root
from endoreg_db.views.media.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    parse_byte_range,
)

logger = logging.getLogger(__name__)
NGINX_PROTECTED_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


class FrameStreamView(APIView):
    """
    Stream a single extracted frame image by video ID and frame number.

    Endpoint:
    - GET /api/media/videos/<video_id>/frames/<frame_number>/stream/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _serve_with_nginx(frame_path: Path, content_type: str) -> HttpResponse | None:
        try:
            relative_path = frame_path.resolve().relative_to(STORAGE_DIR.resolve())
        except ValueError:
            logger.warning(
                "Frame file %s is not inside STORAGE_DIR %s. Falling back to Django file response.",
                frame_path,
                STORAGE_DIR,
            )
            return None

        redirect_url = os.path.join(NGINX_PROTECTED_URL, str(relative_path))
        response = HttpResponse()
        response["Content-Type"] = content_type
        response["X-Accel-Redirect"] = redirect_url
        response["X-Accel-Buffering"] = "no"
        return response

    @staticmethod
    def _expected_relative_path(frame_number: int) -> str:
        return f"frame_{frame_number:07d}.jpg"

    def _assert_video_access_allowed(self, *, request, video: VideoFile) -> None:
        # Streaming access is centralized via RBAC permissions.
        self.check_object_permissions(request, video)

    @staticmethod
    def _validate_frame_path_for_serving(*, video: VideoFile, frame_path: Path) -> Path:
        try:
            resolved_frame_path = frame_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise Http404("Frame file not found on disk") from exc

        try:
            ensure_within_protected_root(resolved_frame_path)
        except ValueError as exc:
            logger.warning(
                "Rejected frame path outside protected data root for video %s: %s",
                getattr(video, "pk", None),
                resolved_frame_path,
            )
            raise Http404("Frame file path is invalid") from exc

        frame_dir = video.get_frame_dir_path()
        if frame_dir is None:
            raise Http404("Video frame directory is not configured")
        try:
            resolved_frame_dir = frame_dir.resolve()
        except Exception as exc:
            raise Http404("Video frame directory path is invalid") from exc

        try:
            resolved_frame_path.relative_to(resolved_frame_dir)
        except ValueError as exc:
            logger.warning(
                "Rejected frame path outside frame_dir for video %s: %s (frame_dir=%s)",
                getattr(video, "pk", None),
                resolved_frame_path,
                resolved_frame_dir,
            )
            raise Http404("Frame file path is invalid") from exc

        return resolved_frame_path

    def _get_or_create_frame_record(
        self, *, video: VideoFile, frame_number: int
    ) -> Frame:
        frame = (
            Frame.objects.select_related("video")
            .filter(video=video, frame_number=frame_number)
            .first()
        )
        if frame is not None:
            return frame

        frame, _created = Frame.objects.get_or_create(
            video=video,
            frame_number=frame_number,
            defaults={
                "relative_path": self._expected_relative_path(frame_number),
                "is_extracted": False,
            },
        )
        return frame

    def _ensure_frame_file_available(self, *, frame: Frame) -> None:
        frame_path = frame.file_path
        if frame_path.exists() and frame_path.is_file():
            if not frame.is_extracted:
                Frame.objects.filter(pk=frame.pk, is_extracted=False).update(
                    is_extracted=True
                )
                frame.is_extracted = True
            return

        video = frame.video
        frame_number = int(frame.frame_number)
        logger.info(
            "Frame file missing for video %s frame %s. Attempting on-demand extraction.",
            video.pk,
            frame_number,
        )

        extraction_error = None
        try:
            video.extract_specific_frame_range(
                start_frame=frame_number,
                end_frame=frame_number + 1,
                overwrite=False,
            )
        except Exception as exc:
            extraction_error = exc
            logger.warning(
                "Range extraction failed for video %s frame %s: %s. Not attempting full extraction in request path.",
                video.pk,
                frame_number,
                exc,
                exc_info=True,
            )
            raise Http404("Frame could not be extracted on demand") from exc

        frame.refresh_from_db()
        frame_path = frame.file_path
        if not frame_path.exists() or not frame_path.is_file():
            if extraction_error is not None:
                logger.error(
                    "On-demand extraction returned without creating frame file for video %s frame %s.",
                    video.pk,
                    frame_number,
                )
            raise Http404("Frame file not found after on-demand extraction")

    def get(self, request, video_id=None, frame_number=None):
        if video_id is None or frame_number is None:
            raise Http404("video_id and frame_number are required")

        try:
            video_id_int = int(video_id)
            frame_number_int = int(frame_number)
        except (TypeError, ValueError):
            raise Http404("Invalid video_id or frame_number format")

        try:
            video = VideoFile.objects.get(pk=video_id_int)
        except VideoFile.DoesNotExist:
            raise Http404(f"Video {video_id_int} not found")
        self._assert_video_access_allowed(request=request, video=video)

        if frame_number_int < 0:
            raise Http404("frame_number must be non-negative")
        if video.frame_count is not None and frame_number_int >= int(video.frame_count):
            raise Http404(
                f"Frame {frame_number_int} out of range for video {video_id_int}"
            )

        frame = self._get_or_create_frame_record(
            video=video,
            frame_number=frame_number_int,
        )

        try:
            self._ensure_frame_file_available(frame=frame)
            frame_path = frame.file_path
        except Exception as exc:
            if isinstance(exc, Http404):
                raise
            logger.error(
                "Failed to resolve frame path for frame %s (video %s): %s",
                frame_number_int,
                video_id_int,
                exc,
                exc_info=True,
            )
            raise Http404("Frame file path could not be resolved")

        if not frame_path.exists() or not frame_path.is_file():
            raise Http404("Frame file not found on disk")
        frame_path = self._validate_frame_path_for_serving(
            video=video, frame_path=frame_path
        )

        mime_type, _ = mimetypes.guess_type(str(frame_path))
        content_type = mime_type or "image/jpeg"

        frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")
        serve_with_nginx = os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"
        if serve_with_nginx:
            nginx_response = self._serve_with_nginx(frame_path, content_type)
            if nginx_response is not None:
                nginx_response["Content-Disposition"] = (
                    f'inline; filename="{frame_path.name}"'
                )
                nginx_response["Access-Control-Allow-Origin"] = frontend_origin
                nginx_response["Access-Control-Allow-Credentials"] = "true"
                return nginx_response

        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")
        file_size = frame_path.stat().st_size
        if range_header:
            try:
                parse_byte_range(range_header, file_size)
            except ValueError:
                response = HttpResponse(status=416, content_type=content_type)
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return add_cors_headers(response, frontend_origin)

        django_file = File(frame_path.open("rb"), name=frame_path.name)
        response = build_partial_content_response(
            field_file=django_file,
            content_type=content_type,
            file_size=file_size,
            range_header=range_header,
            disposition="inline",
            filename=frame_path.name,
        )
        return add_cors_headers(response, frontend_origin)
