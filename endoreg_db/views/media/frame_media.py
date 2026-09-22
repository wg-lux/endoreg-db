from __future__ import annotations

import logging
from typing import Protocol, TypeGuard, cast

from django.http import StreamingHttpResponse
from django.http import Http404, HttpResponse
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from endoreg_db.openapi import OpenApiAPIView as APIView

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.services.video_files import VideoArtifactKind
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import (
    CenterScopedVideoPermission,
    assert_anonymized_center_scope_allowed,
    assert_center_scope_allowed,
)
from endoreg_db.utils.storage_streaming import add_cors_headers
from endoreg_db.services.media_operation_gate import create_video_stream_lease
from endoreg_db.utils.cors import resolve_response_origin
import endoreg_db.utils.frame_stream as frame_stream_utils
from endoreg_db.utils.frame_stream import EncodedFrameSample

logger = logging.getLogger(__name__)


SUPPORTED_DECODED_FRAME_FILE_TYPES: dict[str, VideoArtifactKind] = {
    VideoArtifactKind.RAW.value: VideoArtifactKind.RAW,
    VideoArtifactKind.PROCESSED.value: VideoArtifactKind.PROCESSED,
}


class _ReadVideoFileFrameJpeg(Protocol):
    def __call__(
        self,
        video: VideoFile,
        *,
        frame_number: int,
        file_type: str = "raw",
    ) -> EncodedFrameSample: ...


read_video_file_frame_jpeg = cast(
    _ReadVideoFileFrameJpeg,
    getattr(frame_stream_utils, "read_video_file_frame_jpeg"),
)


def _add_cors_headers_if_configured[_ResponseT: HttpResponse | StreamingHttpResponse](
    response: _ResponseT, frontend_origin: str | None
) -> _ResponseT:
    if frontend_origin is None:
        return response
    return add_cors_headers(response, frontend_origin)


def _parse_int_path_value(value: int | str | None, *, name: str) -> int:
    if value is None:
        raise Http404(f"{name} is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise Http404(f"Invalid {name} format") from exc


def _video_frame_count(video: VideoFile) -> int | None:
    frame_count = getattr(video, "frame_count", None)
    if frame_count is None:
        return None
    return int(frame_count)


def _validate_frame_number_for_video(
    *,
    video: VideoFile,
    video_id: int,
    frame_number: int,
) -> None:
    if frame_number < 0:
        raise Http404("frame_number must be non-negative")
    frame_count = _video_frame_count(video)
    if frame_count is not None and frame_number >= frame_count:
        raise Http404(f"Frame {frame_number} out of range for video {video_id}")


def _field_file_has_name(field_file: object | None) -> TypeGuard[object]:
    return bool(field_file and getattr(field_file, "name", None))


class DecodedFrameStreamView(APIView):
    """
    Decode and return a single frame directly from a video artifact.

    Endpoint:
    - GET /api/media/videos/<video_id>/frames/<frame_number>/decoded-stream/?file_type=raw|processed
    """

    permission_classes = [
        EnvironmentAwarePermission,
        PolicyPermission,
        CenterScopedVideoPermission,
    ]

    processed_only = False

    @classmethod
    def _parse_file_type(
        cls,
        request: Request,
    ) -> tuple[VideoArtifactKind | None, Response | None]:
        raw_value = request.query_params.get("file_type")
        if raw_value is None:
            raw_value = request.query_params.get(
                "type", VideoArtifactKind.PROCESSED.value
            )
        normalized = str(raw_value or "").strip().lower()
        artifact_kind = SUPPORTED_DECODED_FRAME_FILE_TYPES.get(normalized)
        if artifact_kind is None or (
            cls.processed_only and artifact_kind != VideoArtifactKind.PROCESSED
        ):
            return None, Response(
                {
                    "error": "file_type must be one of ['raw', 'processed'].",
                    "details": {"file_type": raw_value},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return artifact_kind, None

    @staticmethod
    def _video_has_selected_artifact(
        video: VideoFile,
        artifact_kind: VideoArtifactKind,
    ) -> bool:
        if artifact_kind == VideoArtifactKind.PROCESSED:
            processed_file = getattr(video, "processed_file", None)
            return _field_file_has_name(processed_file)
        return bool(getattr(video, "has_raw", False))

    def get(
        self,
        request: Request,
        video_id: int | str | None = None,
        frame_number: int | str | None = None,
    ) -> HttpResponse:
        if video_id is None or frame_number is None:
            raise Http404("video_id and frame_number are required")

        video_id_int = _parse_int_path_value(video_id, name="video_id")
        frame_number_int = _parse_int_path_value(frame_number, name="frame_number")

        try:
            video = VideoFile.objects.get(pk=video_id_int)
        except VideoFile.DoesNotExist:
            raise Http404(f"Video {video_id_int} not found")
        self.check_object_permissions(request, video)

        _validate_frame_number_for_video(
            video=video,
            video_id=video_id_int,
            frame_number=frame_number_int,
        )

        artifact_kind, parse_error = self._parse_file_type(request)
        if parse_error is not None:
            return parse_error
        assert artifact_kind is not None

        if artifact_kind == VideoArtifactKind.PROCESSED:
            assert_anonymized_center_scope_allowed(
                request=request,
                obj=video,
                not_found_message="Video not found",
            )
        else:
            assert_center_scope_allowed(
                request=request,
                obj=video,
                not_found_message="Video not found",
            )

        if not self._video_has_selected_artifact(video, artifact_kind):
            raise Http404(
                f"{artifact_kind.value.title()} video artifact is not available"
            )

        try:
            create_video_stream_lease(video, file_type=artifact_kind.value)
            video.refresh_from_db()
            sample = read_video_file_frame_jpeg(
                video,
                frame_number=frame_number_int,
                file_type=artifact_kind.value,
            )
        except FileNotFoundError as exc:
            raise Http404(str(exc)) from exc
        except Exception as exc:
            logger.warning(
                "Decoded frame stream failed for video %s frame %s type=%s: %s",
                video_id_int,
                frame_number_int,
                artifact_kind.value,
                exc,
                exc_info=True,
            )
            return Response(
                {
                    "status": "frame_decode_failed",
                    "video_id": video_id_int,
                    "frame_number": frame_number_int,
                    "file_type": artifact_kind.value,
                    "error": str(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )

        response = HttpResponse(sample.image_bytes, content_type=sample.content_type)
        response["Content-Disposition"] = (
            f'inline; filename="video_{video_id_int}_frame_{frame_number_int:07d}.jpg"'
        )
        response["X-Frame-File-Type"] = artifact_kind.value
        response["X-Frame-Number"] = str(sample.frame_number)
        response["X-Frame-Timestamp"] = str(sample.timestamp)
        response["Cache-Control"] = "no-store"

        frontend_origin = resolve_response_origin(request)
        return _add_cors_headers_if_configured(
            response,
            frontend_origin,
        )


class FrameStreamView(DecodedFrameStreamView):
    """Legacy frame URL using the same in-memory processed-video decoder."""

    processed_only = True
