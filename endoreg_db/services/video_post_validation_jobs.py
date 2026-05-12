from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from endoreg_db.models import VideoFile, VideoProcessingHistory
from endoreg_db.models.state.video_segment_validation import (
    _blackening_history_config,
    _is_outside_frame_blackening_history,
    _resolve_blackening_run_config,
    mark_post_validation_complete,
    mark_post_validation_incomplete,
)
from endoreg_db.config.env import (
    celery_broker_secure_transport_confirmed,
    celery_broker_url_uses_secure_transport,
    celery_frame_extraction_requires_secure_transport,
    get_celery_broker_url,
    get_celery_frame_extraction_queue,
    get_video_post_validation_job_max_workers,
    get_video_post_validation_job_mode,
)
from endoreg_db.services.frame_retention import (
    prune_unused_validated_outside_frames,
)
from endoreg_db.services.video_task_cleanup import rollback_video_frame_artifacts

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=get_video_post_validation_job_max_workers())
ACTIVE_REBUILD_STATUSES = (
    VideoProcessingHistory.STATUS_PENDING,
    VideoProcessingHistory.STATUS_RUNNING,
)
STALE_REBUILD_STATUSES = (VideoProcessingHistory.STATUS_PENDING,)
STALE_REBUILD_TIMEOUT = timedelta(hours=1)
STALE_REBUILD_RUNNING_TIMEOUT = timedelta(hours=7)
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


def _verify_outside_frames_blackened(
    video: VideoFile,
    *,
    only_validated: bool = False,
    tolerance: int = 8,
) -> None:
    """Fail if any metadata-targeted outside frame is missing or visibly non-black."""
    from endoreg_db.models.media.video.video_file_segments import _get_outside_frames

    outside_frames = list(
        _get_outside_frames(video, only_validated=only_validated).only(
            "frame_number",
            "relative_path",
        )
    )
    if not outside_frames:
        return

    import cv2

    missing_files: list[int] = []
    unreadable_files: list[int] = []
    non_black_frames: list[int] = []
    for frame in outside_frames:
        frame_path = frame.file_path
        if not frame_path.is_file():
            missing_files.append(frame.frame_number)
            continue
        image = cv2.imread(frame_path.as_posix())
        if image is None:
            unreadable_files.append(frame.frame_number)
            continue
        if int(image.max()) > tolerance:
            non_black_frames.append(frame.frame_number)

    if missing_files or unreadable_files or non_black_frames:
        raise RuntimeError(
            "Post-validation rebuild did not leave outside frames blackened for "
            f"video {video.pk}: missing_files={missing_files[:10]}, "
            f"unreadable_files={unreadable_files[:10]}, "
            f"non_black_frames={non_black_frames[:10]}"
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


def _ensure_frame_extraction_broker_transport_allowed() -> None:
    if not celery_frame_extraction_requires_secure_transport():
        return
    if celery_broker_secure_transport_confirmed():
        return
    broker_url = get_celery_broker_url()
    if celery_broker_url_uses_secure_transport(broker_url):
        return
    raise RuntimeError(
        "Frame extraction Celery dispatch requires secure broker transport "
        "or CELERY_BROKER_SECURE_TRANSPORT_CONFIRMED=1."
    )


def _active_reprocessing_histories(video: VideoFile):
    return VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status__in=ACTIVE_REBUILD_STATUSES,
    ).order_by("created_at")


def _expire_stale_blackening_histories(video: VideoFile) -> None:
    pending_stale_before = timezone.now() - STALE_REBUILD_TIMEOUT
    pending_histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status__in=STALE_REBUILD_STATUSES,
        created_at__lt=pending_stale_before,
    ).order_by("created_at")
    for history in pending_histories:
        if _is_outside_frame_blackening_history(history):
            history.mark_failure(
                f"Outside-frame blackening job exceeded {STALE_REBUILD_TIMEOUT}."
            )

    running_stale_before = timezone.now() - STALE_REBUILD_RUNNING_TIMEOUT
    running_histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_RUNNING,
        created_at__lt=running_stale_before,
    ).order_by("created_at")
    for history in running_histories:
        if not _is_outside_frame_blackening_history(history):
            continue
        reason = (
            "Outside-frame blackening job was still running after "
            f"{STALE_REBUILD_RUNNING_TIMEOUT}; rolling back extracted frames."
        )
        rollback_video_frame_artifacts(video, reason=reason)
        history.mark_failure(reason)


def _reserve_blackening_history(
    *,
    video: VideoFile,
    task_id: str,
    only_validated: bool,
    queue: str,
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
            config=_blackening_history_config(
                only_validated=only_validated,
                queue=queue,
            ),
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
        if history.status == VideoProcessingHistory.STATUS_SUCCESS:
            return True
        if history.status == VideoProcessingHistory.STATUS_RUNNING:
            history_video = VideoFile.objects.get(pk=video_id)
            rollback_video_frame_artifacts(
                history_video,
                reason=(
                    "Restarting post-validation rebuild for a previously running "
                    f"history {history.pk}."
                ),
            )
        history.mark_running()

    video: VideoFile | None = None
    try:
        run_config = _resolve_blackening_run_config(
            history=history,
            only_validated=only_validated,
        )
        video = VideoFile.objects.get(pk=video_id)
        mark_post_validation_incomplete(video)
        rebuilt = bool(
            VideoFile.create_video_without_outside_frames(
                video, only_validated=run_config.only_validated
            )
        )
        if not rebuilt:
            mark_post_validation_incomplete(video)
            rollback_video_frame_artifacts(
                video,
                reason="Post-validation rebuild returned false.",
            )
            if history is not None:
                history.mark_failure("Outside-frame blackening rebuild returned false.")
            return False

        video.refresh_from_db()
        _verify_extracted_frame_contract(video)
        _verify_outside_frames_blackened(
            video,
            only_validated=run_config.only_validated,
        )
        mark_post_validation_complete(video)
        prune_unused_validated_outside_frames(video)
        if history is not None:
            output_file = getattr(getattr(video, "processed_file", None), "name", "")
            history.mark_success(
                output_file=output_file,
                details="Outside-frame blackening rebuild completed.",
            )
        return True
    except Exception as exc:
        if video is not None:
            mark_post_validation_incomplete(video)
            try:
                rollback_video_frame_artifacts(
                    video,
                    reason=f"Post-validation rebuild failed: {exc}",
                )
            except Exception:
                logger.exception(
                    "Failed to rollback frame artifacts after post-validation "
                    "rebuild failure for video %s.",
                    video.pk,
                )
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
    frame_extraction_queue = get_celery_frame_extraction_queue()
    video = VideoFile.objects.get(pk=video_id)
    history, reservation_status = _reserve_blackening_history(
        video=video,
        task_id=task_id,
        only_validated=only_validated,
        queue=frame_extraction_queue,
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

            _ensure_frame_extraction_broker_transport_allowed()
            async_result = run_video_post_validation_rebuild_task.apply_async(
                args=(int(video_id),),
                kwargs={
                    "only_validated": bool(only_validated),
                    "history_id": history.pk,
                },
                queue=frame_extraction_queue,
                routing_key=frame_extraction_queue,
            )
            _set_history_task_id(history, str(async_result.id))
            return JobDispatchResult(
                task_id=str(async_result.id),
                mode=mode,
                status="queued",
                video_id=int(video_id),
                history_id=history.pk,
            )
        except Exception as exc:
            logger.exception(
                "Celery dispatch failed for video %s.",
                video_id,
            )
            history.mark_failure(str(exc))
            return JobDispatchResult(
                task_id=task_id,
                mode=mode,
                status="failed",
                video_id=int(video_id),
                history_id=history.pk,
            )

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
