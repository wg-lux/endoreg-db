import logging
import mimetypes
import os
from pathlib import Path

from django.http import FileResponse, Http404, HttpResponse
from rest_framework.views import APIView

from endoreg_db.models import Frame, VideoFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.paths import STORAGE_DIR

logger = logging.getLogger(__name__)
NGINX_PROTECTED_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


class FrameStreamView(APIView):
    """
    Stream a single extracted frame image by video ID and frame number.

    Endpoint:
    - GET /api/media/videos/<video_id>/frames/<frame_number>/stream/
    """

    permission_classes = [EnvironmentAwarePermission]

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

    def _get_or_create_frame_record(self, *, video: VideoFile, frame_number: int) -> Frame:
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
                Frame.objects.filter(pk=frame.pk, is_extracted=False).update(is_extracted=True)
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
                "Range extraction failed for video %s frame %s: %s. Falling back to full extraction.",
                video.pk,
                frame_number,
                exc,
                exc_info=True,
            )
            try:
                video.extract_frames(overwrite=False)
            except Exception as full_exc:
                logger.error(
                    "Full frame extraction fallback failed for video %s frame %s: %s",
                    video.pk,
                    frame_number,
                    full_exc,
                    exc_info=True,
                )
                raise Http404("Frame could not be extracted on demand") from full_exc

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

        mime_type, _ = mimetypes.guess_type(str(frame_path))
        content_type = mime_type or "image/jpeg"

        frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")
        serve_with_nginx = os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"
        if serve_with_nginx:
            nginx_response = self._serve_with_nginx(frame_path, content_type)
            if nginx_response is not None:
                nginx_response["Content-Disposition"] = f'inline; filename="{frame_path.name}"'
                nginx_response["Access-Control-Allow-Origin"] = frontend_origin
                nginx_response["Access-Control-Allow-Credentials"] = "true"
                return nginx_response

        file_handle = open(frame_path, "rb")
        response = FileResponse(file_handle, content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{frame_path.name}"'
        response["Access-Control-Allow-Origin"] = frontend_origin
        response["Access-Control-Allow-Credentials"] = "true"
        return response
