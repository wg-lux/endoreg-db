from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from endoreg_db.models import VideoFile, VideoProcessingHistory
from endoreg_db.config.env import (
    get_video_post_validation_job_max_workers,
    get_video_post_validation_job_mode,
)

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=get_video_post_validation_job_max_workers())
OUTSIDE_FRAME_BLACKENING_KIND = "outside_frame_blackening"
ACTIVE_REBUILD_STATUSES = (
    VideoProcessingHistory.STATUS_PENDING,
    VideoProcessingHistory.STATUS_RUNNING,
)
STALE_REBUILD_STATUSES = (VideoProcessingHistory.STATUS_PENDING,)
STALE_REBUILD_TIMEOUT = timedelta(hours=1)
RESERVATION_CREATED = "created"
RESERVATION_ALREADY_QUEUED = "already_queued"
RESERVATION_BUSY = "busy"


def _verify_extracted_frame_contract(video) -> None:
    """Fail if post-validation rebuild did not leave stable frames available."""
    from endoreg_db.models import Frame

    state = video.get_or_create_state()
    if not state.frames_extracted:
        raise RuntimeError(
            f"Post-validation rebuild for video {video.pk} did not leave extracted frames available."
        )

    expected_count = video.frame_count or state.frame_count
    if expected_count is None:
        expected_count = Frame.objects.filter(video=video).count()
    expected_count = int(expected_count or 0)
    if expected_count <= 0:
        raise RuntimeError(
            f"Post-validation rebuild for video {video.pk} has no stable frame count."
        )

    frame_dir = video.get_frame_dir_path()
    if frame_dir is None:
        raise RuntimeError(
            f"Post-validation rebuild for video {video.pk} has no frame directory."
        )

    frames = list(
        Frame.objects.filter(
            video=video,
            frame_number__gte=0,
            frame_number__lt=expected_count,
        ).only("frame_number", "relative_path", "is_extracted")
    )
    if len(frames) != expected_count:
        raise RuntimeError(
            "Post-validation rebuild left frames in a non-recreatable state: "
            "did not preserve exact Frame DB rows for "
            f"video {video.pk}: expected={expected_count}, actual={len(frames)}"
        )

    missing_files: list[int] = []
    unstable_rows: list[tuple[int, str]] = []
    unextracted_rows: list[int] = []
    for frame in frames:
        expected_relative_path = f"frame_{frame.frame_number:07d}.jpg"
        if frame.relative_path != expected_relative_path:
            unstable_rows.append((frame.frame_number, frame.relative_path))
        if not frame.is_extracted:
            unextracted_rows.append(frame.frame_number)
        if not (frame_dir / expected_relative_path).is_file():
            missing_files.append(frame.frame_number)

    if missing_files or unstable_rows or unextracted_rows:
        raise RuntimeError(
            "Post-validation rebuild left frames in a non-recreatable state for "
            f"video {video.pk}: missing_files={missing_files[:10]}, "
            f"unstable_rows={unstable_rows[:10]}, "
            f"unextracted_rows={unextracted_rows[:10]}"
        )


@dataclass(frozen=True)
class JobDispatchResult:
    task_id: str
    mode: str
    status: str
    video_id: int
    history_id: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _blackening_history_config(*, only_validated: bool) -> dict[str, object]:
    return {
        "kind": OUTSIDE_FRAME_BLACKENING_KIND,
        "only_validated": bool(only_validated),
    }


def _is_outside_frame_blackening_history(history: VideoProcessingHistory) -> bool:
    config = history.config if isinstance(history.config, dict) else {}
    return config.get("kind") == OUTSIDE_FRAME_BLACKENING_KIND


def _active_reprocessing_histories(video: VideoFile):
    return VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status__in=ACTIVE_REBUILD_STATUSES,
    ).order_by("created_at")


def _expire_stale_blackening_histories(video: VideoFile) -> None:
    stale_before = timezone.now() - STALE_REBUILD_TIMEOUT
    stale_histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status__in=STALE_REBUILD_STATUSES,
        created_at__lt=stale_before,
    ).order_by("created_at")
    for history in stale_histories:
        if _is_outside_frame_blackening_history(history):
            history.mark_failure(
                f"Outside-frame blackening job exceeded {STALE_REBUILD_TIMEOUT}."
            )


def _reserve_blackening_history(
    *,
    video: VideoFile,
    task_id: str,
    only_validated: bool,
) -> tuple[VideoProcessingHistory, str]:
    with transaction.atomic():
        locked_video = VideoFile.objects.select_for_update().get(pk=video.pk)
        _expire_stale_blackening_histories(locked_video)
        outside_history: VideoProcessingHistory | None = None
        active_histories = _active_reprocessing_histories(
            locked_video
        ).select_for_update()
        for history in active_histories:
            if _is_outside_frame_blackening_history(history):
                if outside_history is None:
                    outside_history = history
                continue
            return history, RESERVATION_BUSY

        if outside_history is not None:
            return outside_history, RESERVATION_ALREADY_QUEUED

        history = VideoProcessingHistory.objects.create(
            video=locked_video,
            operation=VideoProcessingHistory.OPERATION_REPROCESSING,
            status=VideoProcessingHistory.STATUS_PENDING,
            task_id=task_id,
            config=_blackening_history_config(only_validated=only_validated),
        )
        return history, RESERVATION_CREATED


def _set_history_task_id(history: VideoProcessingHistory, task_id: str) -> None:
    if history.task_id == task_id:
        return
    history.task_id = task_id
    history.save(update_fields=["task_id"])


def _get_processing_history(
    history_id: int | None,
) -> VideoProcessingHistory | None:
    if history_id is None:
        return None
    try:
        return VideoProcessingHistory.objects.get(pk=history_id)
    except VideoProcessingHistory.DoesNotExist:
        logger.warning("VideoProcessingHistory %s not found.", history_id)
        return None


def _run_video_post_validation_rebuild(
    video_id: int,
    *,
    only_validated: bool = False,
    history_id: int | None = None,
) -> bool:
    history = _get_processing_history(history_id)
    if history is not None:
        history.mark_running()

    try:
        video = VideoFile.objects.get(pk=video_id)
        rebuilt = bool(
            VideoFile.create_video_without_outside_frames(
                video, only_validated=only_validated
            )
        )
        if not rebuilt:
            if history is not None:
                history.mark_failure("Outside-frame blackening rebuild returned false.")
            return False

        video.refresh_from_db()
        _verify_extracted_frame_contract(video)
        if history is not None:
            output_file = getattr(getattr(video, "processed_file", None), "name", "")
            history.mark_success(
                output_file=output_file,
                details="Outside-frame blackening rebuild completed.",
            )
        return True
    except Exception as exc:
        if history is not None:
            history.mark_failure(str(exc))
        raise


def dispatch_video_post_validation_rebuild(
    *,
    video_id: int,
    only_validated: bool = False,
) -> JobDispatchResult:
    """
    Dispatch expensive post-validation video processing out of the request path.

    Modes (env `VIDEO_POST_VALIDATION_JOB_MODE`):
    - `celery` (default): queue to Celery worker and return immediately
    - `thread`: queue to process-local executor and return immediately
    - `inline`: run synchronously (useful in local debugging/tests)
    """
    mode = get_video_post_validation_job_mode()
    task_id = str(uuid.uuid4())
    video = VideoFile.objects.get(pk=video_id)
    history, reservation_status = _reserve_blackening_history(
        video=video,
        task_id=task_id,
        only_validated=only_validated,
    )

    if reservation_status == RESERVATION_BUSY:
        return JobDispatchResult(
            task_id=history.task_id or "",
            mode=mode,
            status="busy",
            video_id=int(video_id),
            history_id=history.pk,
        )

    if reservation_status == RESERVATION_ALREADY_QUEUED:
        return JobDispatchResult(
            task_id=history.task_id or "",
            mode=mode,
            status="already_queued",
            video_id=int(video_id),
            history_id=history.pk,
        )

    if mode == "inline":
        rebuilt = _run_video_post_validation_rebuild(
            video_id,
            only_validated=only_validated,
            history_id=history.pk,
        )
        return JobDispatchResult(
            task_id=task_id,
            mode=mode,
            status="completed" if rebuilt else "failed",
            video_id=int(video_id),
            history_id=history.pk,
        )

    if mode == "celery":
        try:
            from endoreg_db.tasks import run_video_post_validation_rebuild_task

            async_result = run_video_post_validation_rebuild_task.delay(
                int(video_id),
                only_validated=bool(only_validated),
                history_id=history.pk,
            )
            _set_history_task_id(history, str(async_result.id))
            return JobDispatchResult(
                task_id=str(async_result.id),
                mode=mode,
                status="queued",
                video_id=int(video_id),
                history_id=history.pk,
            )
        except Exception:
            logger.exception(
                "Celery dispatch failed for video %s. Falling back to thread mode.",
                video_id,
            )
            mode = "thread"

    def _job():
        try:
            _run_video_post_validation_rebuild(
                video_id,
                only_validated=only_validated,
                history_id=history.pk,
            )
        except Exception:
            logger.exception(
                "Async post-validation rebuild failed for video %s (task_id=%s)",
                video_id,
                task_id,
            )

    try:
        _executor.submit(_job)
    except Exception as exc:
        history.mark_failure(str(exc))
        raise
    return JobDispatchResult(
        task_id=task_id,
        mode=mode,
        status="queued",
        video_id=int(video_id),
        history_id=history.pk,
    )
