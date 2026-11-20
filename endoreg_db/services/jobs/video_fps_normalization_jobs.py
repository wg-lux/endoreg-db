from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.schemas.video_jobs import (
    FPS_NORMALIZATION_CONFIG_OPERATION,
    MAX_SEGMENTATION_FPS,
)
from endoreg_db.services.media.operation_gate import defer_if_video_media_busy
from endoreg_db.services.video_files import get_video_fps, require_persisted_video_fps
from endoreg_db.services.video_storage.processed_transcode import (
    transcode_processed_video_for_storage_pressure,
)

CONFIG_OPERATION = FPS_NORMALIZATION_CONFIG_OPERATION


@dataclass(frozen=True)
class FpsNormalizationDispatchResult:
    video_id: int
    status: str
    fps: float | None
    max_fps: float
    task_id: str = ""
    history_id: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalization_status(video: VideoFile) -> FpsNormalizationDispatchResult:
    """Report segmentation readiness; the native source FPS is accepted as-is."""
    fps = require_persisted_video_fps(video)
    return FpsNormalizationDispatchResult(
        video_id=int(video.pk),
        status="ready",
        fps=fps,
        max_fps=MAX_SEGMENTATION_FPS,
    )


def _run_video_fps_normalization(  # pyright: ignore[reportUnusedFunction]
    video_id: int, history_id: int
) -> bool:
    history = VideoProcessingHistory.objects.get(pk=history_id, video_id=video_id)
    if history.status == history.STATUS_SUCCESS:
        return True
    history.mark_running()
    try:
        defer_if_video_media_busy(video_id=video_id, history=history)
        video = VideoFile.objects.get(pk=video_id)
        result = transcode_processed_video_for_storage_pressure(
            video,
            apply=True,
            quality_mode="quality",
            allow_larger=True,
            resample_max_fps=MAX_SEGMENTATION_FPS,
        )
        if not result.changed:
            raise RuntimeError(
                result.detail or f"Normalization ended with {result.status}."
            )

        normalized_video = VideoFile.objects.get(pk=video_id)
        normalized_fps = float(get_video_fps(normalized_video))
        if (
            not math.isfinite(normalized_fps)
            or normalized_fps <= 0
            or normalized_fps > MAX_SEGMENTATION_FPS
        ):
            raise RuntimeError(
                f"Normalized output FPS is outside contract: {normalized_fps:g}."
            )
        normalized_frame_count = normalized_video.frame_count
        if normalized_frame_count is None or normalized_frame_count <= 0:
            raise RuntimeError("Normalized video has no positive frame count.")
        history.mark_success(
            output_file=str(getattr(normalized_video.processed_file, "name", "")),
            details=f"Segmentation FPS normalized to {normalized_fps:g} fps.",
        )
        return True
    except Exception as exc:
        history.mark_failure(str(exc))
        raise


def dispatch_video_fps_normalization(
    video: VideoFile,
) -> FpsNormalizationDispatchResult:
    """Segmentation no longer caps FPS, so no resampling job is queued."""
    return normalization_status(video)
