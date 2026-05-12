import logging
import mimetypes
from pathlib import Path

from django.core.files import File
from django.http import Http404, HttpResponse, HttpResponseBase
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.models import Frame, VideoFile
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.services.frame_extraction_jobs import (
    FrameExtractionDispatchResult,
    REQUEST_STATUS_FAILED,
    get_or_create_frame_record,
    request_frame_extraction,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.paths import (
    ensure_within_protected_media_root,
)
from endoreg_db.utils.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    parse_byte_range,
)
from endoreg_db.utils.nginx_accel import (
    build_nginx_accel_response_for_path,
    nginx_offload_enabled,
)
from endoreg_db.utils.cors import resolve_response_origin

logger = logging.getLogger(__name__)


class FrameStreamView(APIView):
    """
    Stream a single extracted frame image by video ID and frame number.

    Endpoint:
    - GET /api/media/videos/<video_id>/frames/<frame_number>/stream/ (Constructed from frame stream helper)
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _serve_with_nginx(
        frame_path: Path,
        content_type: str,
        *,
        frontend_origin: str,
    ) -> HttpResponseBase | None:
        try:
            return build_nginx_accel_response_for_path(
                path=frame_path,
                content_type=content_type,
                filename=frame_path.name,
                disposition="inline",
                frontend_origin=frontend_origin,
            )
        except ValueError:
            logger.warning(
                "Frame file %s is outside the configured protected media root. Falling back to Django file response.",
                frame_path,
            )
            return None

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
            resolved_frame_path = ensure_within_protected_media_root(
                resolved_frame_path
            )
        except ValueError as exc:
            logger.warning(
                "Rejected frame path outside protected media root for video %s: %s",
                getattr(video, "pk", None),
                resolved_frame_path,
            )
            raise Http404("Frame file path is invalid") from exc

        frame_dir = video.get_frame_dir_path()
        if frame_dir is None:
            raise Http404("Video frame directory is not configured")
        try:
            resolved_frame_dir = ensure_within_protected_media_root(frame_dir)
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

    def _ensure_frame_file_available(self, *, frame: Frame) -> Response | None:
        frame_path = frame.file_path
        if frame_path.exists() and frame_path.is_file():
            if not frame.is_extracted:
                Frame.objects.filter(pk=frame.pk, is_extracted=False).update(
                    is_extracted=True
                )
                frame.is_extracted = True
            return None

        dispatch_result: FrameExtractionDispatchResult = request_frame_extraction(
            video=frame.video,
            frame_number=int(frame.frame_number),
        )
        payload = {
            "status": (
                "frame_extraction_failed"
                if dispatch_result.status == REQUEST_STATUS_FAILED
                else "frame_extraction_pending"
            ),
            "video_id": int(frame.video_id),
            "frame_number": int(frame.frame_number),
            "request_id": int(dispatch_result.request_id),
            "task_id": dispatch_result.task_id,
        }
        if dispatch_result.status == REQUEST_STATUS_FAILED:
            return Response(payload, status=status.HTTP_409_CONFLICT)
        return Response(payload, status=status.HTTP_202_ACCEPTED)

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

        frame = get_or_create_frame_record(
            video=video,
            frame_number=frame_number_int,
        )

        pending_response = self._ensure_frame_file_available(frame=frame)
        if pending_response is not None:
            return pending_response
        frame_path = frame.file_path

        if not frame_path.exists() or not frame_path.is_file():
            raise Http404("Frame file not found on disk")
        frame_path = self._validate_frame_path_for_serving(
            video=video, frame_path=frame_path
        )

        mime_type, _ = mimetypes.guess_type(str(frame_path))
        content_type = mime_type or "image/jpeg"

        frontend_origin = resolve_response_origin(request)

        if nginx_offload_enabled():
            nginx_response = self._serve_with_nginx(
                frame_path,
                content_type,
                frontend_origin=frontend_origin,
            )
            if nginx_response is not None:
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
