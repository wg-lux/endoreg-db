from typing import Protocol, cast, runtime_checkable

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from lx_dtypes.models.meta.VideoMeta import (
    VideoMetadataAnonymizationState,
    VideoMetadataStatsPayload,
    VideoMetadataStatus,
)

from endoreg_db.config.env import DEFAULT_VIDEO_FPS
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.lx_video_contracts import resolve_lx_anonymization_state
from endoreg_db.services.video_files import get_video_outside_segments
from endoreg_db.utils.permissions import EnvironmentAwarePermission


@runtime_checkable
class _NamedRelation(Protocol):
    name: str


@runtime_checkable
class _VideoMetaWithRoi(Protocol):
    has_roi: bool


class _VideoState(Protocol):
    anonymized: bool


class _VideoMetadataSource(Protocol):
    duration: float | None
    fps: float | None
    width: int | None
    height: int | None
    state: _VideoState | None
    center: object | None
    processor: object | None
    frame_count: int | None
    video_meta: object | None
    original_file_name: str | None


def _relation_display_name(relation: object | None) -> str:
    if relation is None:
        return "Unbekannt"
    if isinstance(relation, _NamedRelation):
        return relation.name
    return str(relation)


def _has_roi(video_meta: object | None) -> bool:
    return isinstance(video_meta, _VideoMetaWithRoi) and video_meta.has_roi


class VideoMetadataStatsView(APIView):
    """
    GET media/videos/{pk}/metadata/ - Get comprehensive video metadata.

    Merges logic from:
    1. VideoFile model (duration, fps, resolution)
    2. VideoState model (status, anonymization flags)
    3. Related models (Center, Processor, SensitiveMeta)
    """

    permission_classes = [EnvironmentAwarePermission]

    def get(self, request: Request, pk: int) -> Response:
        # Prefetch related fields to avoid N+1 queries since we access them all
        video_model = get_object_or_404(
            VideoFile.objects.select_related(
                "state", "center", "processor", "video_meta", "sensitive_meta"
            ),
            pk=pk,
        )
        video = cast(_VideoMetadataSource, video_model)

        # --- 1. Basic Specs (from VideoFile) ---
        # Use model fields, defaulting if None
        duration = video.duration if video.duration is not None else 0.0
        fps = video.fps if video.fps is not None else DEFAULT_VIDEO_FPS

        resolution = "BLANK"
        if video.width and video.height:
            resolution = f"{video.width}x{video.height}"

        # --- 2. Status & State (from VideoState) ---
        status_val: VideoMetadataStatus = "BLANK"
        is_anonymized = False

        state = video.state
        if state is not None:
            lx_status = resolve_lx_anonymization_state(video_model)
            status_val = VideoMetadataAnonymizationState(lx_status.value)

            is_anonymized = (
                status_val
                == VideoMetadataAnonymizationState.DONE_PROCESSING_ANONYMIZATION
                or state.anonymized
            )

        # --- 3. Relations (Center / Processor) ---
        center_name = _relation_display_name(video.center)

        processor_name = _relation_display_name(video.processor)

        # --- 4. Deep Inference (SensitiveMeta / VideoMeta) ---
        sensitive_count: int | None = None
        total_frames = video.frame_count  # Direct from VideoFile model
        sensitive_ratio: float | None = None

        outside_segments = get_video_outside_segments(video_model, only_validated=False)
        outside_frame_count = outside_segments.count()

        # Try to get ROI data (from video_meta relation)
        has_roi = _has_roi(video.video_meta)

        # --- 5. Construct Response ---
        metadata = VideoMetadataStatsPayload.model_validate(
            {
                # -- Frontend VideoMeta Requirements --
                "id": pk,
                "original_file_name": video.original_file_name or f"Video {pk}",
                "status": status_val,
                "assigned_user": "BLANK",
                "anonymized": is_anonymized,
                "duration": duration,
                "fps": fps,
                "has_roi": has_roi,
                "outside_frame_count": outside_frame_count,
                "center_name": center_name,
                "processor_name": processor_name,
                "sensitive_frame_count": sensitive_count,
                "total_frames": total_frames,
                "sensitive_ratio": sensitive_ratio,
                "resolution": resolution,
            }
        )

        return Response(metadata.model_dump(mode="json"), status=status.HTTP_200_OK)
