from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass

from django.conf import settings
from django.db import transaction
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.schemas.video_jobs import (
    FPS_NORMALIZATION_CONFIG_OPERATION,
    FpsNormalizationHistoryConfig,
    MAX_SEGMENTATION_FPS,
)
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.jobs.stale_recovery import (
    recover_stale_video_processing_history,
)
from endoreg_db.services.media_operation_gate import defer_if_video_media_busy
from endoreg_db.services.video_files import get_video_fps, require_persisted_video_fps
from endoreg_db.services.video_processed_transcode import (
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


def _active_history(video: VideoFile) -> VideoProcessingHistory | None:
    return (
        VideoProcessingHistory.objects.filter(
            video=video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            status__in=(
                VideoProcessingHistory.STATUS_PENDING,
                VideoProcessingHistory.STATUS_RUNNING,
            ),
            config__operation=CONFIG_OPERATION,
        )
        .order_by("created_at")
        .first()
    )


def normalization_status(video: VideoFile) -> FpsNormalizationDispatchResult:
    fps = require_persisted_video_fps(video)
    active = _active_history(video)
    if active is not None:
        return FpsNormalizationDispatchResult(
            video_id=int(video.pk),
            status="running" if active.status == active.STATUS_RUNNING else "queued",
            fps=fps,
            max_fps=MAX_SEGMENTATION_FPS,
            task_id=active.task_id,
            history_id=int(active.pk),
        )
    latest = (
        VideoProcessingHistory.objects.filter(
            video=video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            config__operation=CONFIG_OPERATION,
        )
        .order_by("-created_at")
        .first()
    )
    status = "ready" if 0 < fps <= MAX_SEGMENTATION_FPS else "required"
    detail = ""
    if (
        status != "ready"
        and latest is not None
        and latest.status == latest.STATUS_FAILURE
    ):
        status = "failed"
        detail = latest.details
    return FpsNormalizationDispatchResult(
        video_id=int(video.pk),
        status=status,
        fps=fps,
        max_fps=MAX_SEGMENTATION_FPS,
        task_id=latest.task_id if latest is not None else "",
        history_id=int(latest.pk) if latest is not None else None,
        detail=detail,
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
    current_fps = require_persisted_video_fps(video)
    if 0 < current_fps <= MAX_SEGMENTATION_FPS:
        return FpsNormalizationDispatchResult(
            video_id=int(video.pk),
            status="ready",
            fps=current_fps,
            max_fps=MAX_SEGMENTATION_FPS,
        )

    queue = queue_for_job_kind(HeavyJobKind.VIDEO_TRANSCODE)
    task_id = str(uuid.uuid4())
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
        if LabelVideoSegment.objects.filter(video_file=locked_video).exists():
            raise ValueError(
                "FPS normalization must run before segment rows exist; refusing to "
                "invalidate existing clinical frame coordinates."
            )
        active_histories = (
            VideoProcessingHistory.objects.filter(
                video=locked_video,
                operation=VideoProcessingHistory.OPERATION_REPROCESSING,
                status__in=(
                    VideoProcessingHistory.STATUS_PENDING,
                    VideoProcessingHistory.STATUS_RUNNING,
                ),
                config__operation=CONFIG_OPERATION,
            )
            .order_by("created_at")
            .select_for_update()
        )
        existing = next(
            (
                history
                for history in active_histories
                if not recover_stale_video_processing_history(
                    history,
                    job_name="FPS normalization",
                )
            ),
            None,
        )
        if existing is not None:
            return FpsNormalizationDispatchResult(
                video_id=int(video.pk),
                status="already_queued",
                fps=current_fps,
                max_fps=MAX_SEGMENTATION_FPS,
                task_id=existing.task_id,
                history_id=int(existing.pk),
            )
        config = FpsNormalizationHistoryConfig(queue=queue)
        history = VideoProcessingHistory.objects.create(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            status=VideoProcessingHistory.STATUS_PENDING,
            task_id=task_id,
            config=config.model_dump(mode="json"),
        )

    from endoreg_db.tasks import run_video_fps_normalization_task

    if bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)):
        run_video_fps_normalization_task.apply(args=(int(video.pk), int(history.pk)))
        return normalization_status(VideoFile.objects.get(pk=video.pk))

    try:
        ensure_secure_transport_for_job_kind(HeavyJobKind.VIDEO_TRANSCODE)
        async_result = run_video_fps_normalization_task.apply_async(
            args=(int(video.pk), int(history.pk)),
            queue=queue,
            routing_key=queue,
        )
    except Exception as exc:
        history.mark_failure(str(exc))
        raise
    if str(async_result.id) != task_id:
        history.task_id = str(async_result.id)
        history.save(update_fields=["task_id"])
    return FpsNormalizationDispatchResult(
        video_id=int(video.pk),
        status="queued",
        fps=current_fps,
        max_fps=MAX_SEGMENTATION_FPS,
        task_id=str(async_result.id),
        history_id=int(history.pk),
    )
