# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import cast

from django.db import transaction
from django.utils import timezone

from endoreg_db.config.env import (
    celery_broker_transport_error,
    celery_ffmpeg_media_requires_secure_transport,
    get_celery_ffmpeg_media_queue,
    get_video_post_validation_dispatch_delay_seconds,
    get_video_post_validation_job_max_workers,
    get_video_post_validation_job_mode,
)
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.services.video_segment_validation_workflow import (
    blackening_history_config,
    is_outside_frame_blackening_history,
    mark_post_validation_complete,
    mark_post_validation_incomplete,
    resolve_blackening_run_config,
)
from endoreg_db.services.frame_retention import (
    prune_unused_validated_outside_frames,
)
from endoreg_db.services.jobs.video_task_cleanup import rollback_video_frame_artifacts
from endoreg_db.services.media_operation_gate import (
    MediaOperationDeferred,
    defer_if_video_media_busy,
)
from endoreg_db.services.video_files import (
    censor_outside_video_frames,
    ensure_local_processed_video_file,
    extract_video_frames,
)
from endoreg_db.services.video_post_validation_blackening import (
    merge_outside_frame_intervals as _merge_outside_frame_intervals,
)
from endoreg_db.services.video_post_validation_blackening import (
    rebuild_processed_video_without_outside_frames,
)
from endoreg_db.services.video_temporal_inference import (
    dispatch_deferred_temporal_inference_after_rebuild,
    fail_deferred_temporal_inference_for_rebuild,
)

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


def _capture_frame(video_path: Path, frame_number: int):
    import cv2

    capture = cv2.VideoCapture(video_path.as_posix())
    try:
        if not capture.isOpened():
            raise RuntimeError(
                f"Could not open rebuilt video for sampling: {video_path}"
            )
        if not capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_number)):
            raise RuntimeError(
                f"Could not seek rebuilt video to frame {frame_number}: {video_path}"
            )
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(
                f"Could not decode rebuilt video frame {frame_number}: {video_path}"
            )
        return frame
    finally:
        capture.release()


def _verify_processed_video_contract(
    video: VideoFile,
    *,
    only_validated: bool = False,
    outside_intervals: Sequence[tuple[int, int]] | None = None,
    tolerance: int = 8,
) -> None:
    with ensure_local_processed_video_file(video) as processed_path:
        if not processed_path.is_file():
            raise RuntimeError(
                f"Post-validation rebuild for video {video.pk} did not leave a processed file."
            )
        if processed_path.stat().st_size <= 0:
            raise RuntimeError(
                f"Post-validation rebuild for video {video.pk} produced an empty processed file."
            )

        from endoreg_db.utils.ffmpeg_wrapper import get_stream_info

        probe_data = get_stream_info(processed_path)
        probe_data_dict = (
            cast(dict[str, object], probe_data) if isinstance(probe_data, dict) else {}
        )
        streams_value = probe_data_dict.get("streams", [])
        streams = (
            cast(list[object], streams_value) if isinstance(streams_value, list) else []
        )
        has_video_stream = False
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            if cast(dict[str, object], stream).get("codec_type") == "video":
                has_video_stream = True
                break
        if not has_video_stream:
            raise RuntimeError(
                f"Post-validation rebuild for video {video.pk} produced no probeable video stream."
            )

        intervals = (
            list(outside_intervals)
            if outside_intervals is not None
            else _merge_outside_frame_intervals(
                video,
                only_validated=only_validated,
            )
        )
        if not intervals:
            return

        inside_sample_frames = sorted({start for start, _end in intervals})
        outside_sample_frames: list[int] = []
        previous_end = -1
        for start_frame, _end_frame in intervals:
            candidate = max(previous_end + 1, 0)
            if candidate < start_frame:
                outside_sample_frames.append(candidate)
            previous_end = _end_frame
            if outside_sample_frames:
                break

        for frame_number in inside_sample_frames[:3]:
            frame = _capture_frame(processed_path, frame_number)
            if int(frame.max()) > tolerance:
                raise RuntimeError(
                    "Post-validation rebuild did not leave outside frames blackened for "
                    f"video {video.pk}: frame_number={frame_number}"
                )

        for frame_number in outside_sample_frames[:3]:
            frame = _capture_frame(processed_path, frame_number)
            if int(frame.max()) <= tolerance:
                raise RuntimeError(
                    "Post-validation rebuild unexpectedly blackened non-outside frames for "
                    f"video {video.pk}: frame_number={frame_number}"
                )


def _verify_outside_frames_blackened(
    video: VideoFile,
    *,
    only_validated: bool = False,
    tolerance: int = 8,
) -> None:
    """Fail if any metadata-targeted outside frame is missing or visibly non-black."""
    from endoreg_db.services.video_files._segments import _get_outside_frames

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


def _verify_extracted_frame_contract(
    video: VideoFile, *, extension: str = "jpg"
) -> None:
    """Require one complete, stable frame file for every persisted frame index."""
    state = video.get_or_create_state()
    state.refresh_from_db()
    if not state.frames_extracted:
        raise RuntimeError(
            f"Post-validation rebuild did not extract frames for video {video.pk}."
        )

    video_frame_count = video.frame_count
    state_frame_count = state.frame_count
    if (
        video_frame_count is not None
        and state_frame_count is not None
        and int(video_frame_count) != int(state_frame_count)
    ):
        raise RuntimeError(
            "Post-validation frame count mismatch for video "
            f"{video.pk}: video={video_frame_count}, state={state_frame_count}."
        )
    expected_frame_count = (
        int(video_frame_count)
        if video_frame_count is not None
        else int(state_frame_count)
        if state_frame_count is not None
        else 0
    )
    if expected_frame_count <= 0:
        raise RuntimeError(
            f"Post-validation rebuild has no positive frame count for video {video.pk}."
        )

    frame_dir = video.get_frame_dir_path()
    if frame_dir is None or not frame_dir.is_dir():
        raise RuntimeError(
            f"Post-validation rebuild has no frame directory for video {video.pk}."
        )

    invalid_numbers: list[tuple[int, int]] = []
    invalid_names: list[int] = []
    unextracted_frames: list[int] = []
    missing_files: list[int] = []
    observed_frame_count = 0
    frames = video.frames.only(
        "frame_number",
        "relative_path",
        "is_extracted",
    ).order_by("frame_number")
    for expected_frame_number, frame in enumerate(frames.iterator(chunk_size=2000)):
        observed_frame_count += 1
        frame_number = int(frame.frame_number)
        if frame_number != expected_frame_number and len(invalid_numbers) < 10:
            invalid_numbers.append((expected_frame_number, frame_number))
        expected_name = f"frame_{frame_number:07d}.{extension}"
        if str(frame.relative_path) != expected_name and len(invalid_names) < 10:
            invalid_names.append(frame_number)
        if not frame.is_extracted and len(unextracted_frames) < 10:
            unextracted_frames.append(frame_number)
        if not (frame_dir / expected_name).is_file() and len(missing_files) < 10:
            missing_files.append(frame_number)

    if (
        observed_frame_count != expected_frame_count
        or invalid_numbers
        or invalid_names
        or unextracted_frames
        or missing_files
    ):
        raise RuntimeError(
            "Post-validation rebuild left an incomplete frame cache for video "
            f"{video.pk}: expected_count={expected_frame_count}, "
            f"observed_count={observed_frame_count}, "
            f"invalid_numbers={invalid_numbers}, invalid_names={invalid_names}, "
            f"unextracted_frames={unextracted_frames}, missing_files={missing_files}."
        )


@dataclass(frozen=True)
class JobDispatchResult:
    task_id: str
    mode: str
    status: str
    video_id: int
    history_id: int | None = None
    validation_status: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        if data["validation_status"] is None:
            data["validation_status"] = _validation_status_for_job_status(self.status)
        return data


def _validation_status_for_job_status(job_status: str) -> str:
    if job_status in {"queued", "already_queued"}:
        return "scheduled"
    if job_status == "busy":
        return "running"
    if job_status in {"completed", "noop"}:
        return "completed"
    if job_status == "failed":
        return "failed"
    return "scheduled"


def _job_dispatch_result(
    *,
    task_id: str,
    mode: str,
    status: str,
    video_id: int,
    history_id: int | None = None,
) -> JobDispatchResult:
    return JobDispatchResult(
        task_id=task_id,
        mode=mode,
        status=status,
        video_id=int(video_id),
        history_id=history_id,
        validation_status=_validation_status_for_job_status(status),
    )


def _ensure_ffmpeg_media_broker_transport_allowed() -> None:
    error = celery_broker_transport_error(
        require_secure_transport=celery_ffmpeg_media_requires_secure_transport(),
        workload="FFmpeg media Celery",
    )
    if error is None:
        return
    raise RuntimeError(error)


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
        if is_outside_frame_blackening_history(history):
            reason = f"Outside-frame blackening job exceeded {STALE_REBUILD_TIMEOUT}."
            history.mark_failure(reason)
            fail_deferred_temporal_inference_for_rebuild(
                video_id=video.pk,
                rebuild_history_id=history.pk,
                reason=(
                    "Temporal inference was not queued because the required "
                    f"frame rebuild failed: {reason}"
                ),
            )

    running_stale_before = timezone.now() - STALE_REBUILD_RUNNING_TIMEOUT
    running_histories = VideoProcessingHistory.objects.filter(
        video=video,
        operation=VideoProcessingHistory.OPERATION_REPROCESSING,
        status=VideoProcessingHistory.STATUS_RUNNING,
        created_at__lt=running_stale_before,
    ).order_by("created_at")
    for history in running_histories:
        if not is_outside_frame_blackening_history(history):
            continue
        reason = (
            "Outside-frame blackening job was still running after "
            f"{STALE_REBUILD_RUNNING_TIMEOUT}; rolling back extracted frames."
        )
        rollback_video_frame_artifacts(video, reason=reason)
        history.mark_failure(reason)
        fail_deferred_temporal_inference_for_rebuild(
            video_id=video.pk,
            rebuild_history_id=history.pk,
            reason=(
                "Temporal inference was not queued because the required "
                f"frame rebuild failed: {reason}"
            ),
        )


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
            if is_outside_frame_blackening_history(history):
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
            config=blackening_history_config(
                only_validated=only_validated,
                queue=queue,
            ),
        )
        mark_post_validation_incomplete(locked_video)
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
        defer_if_video_media_busy(video_id=video_id, history=history)
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
        run_config = resolve_blackening_run_config(
            history=history,
            only_validated=only_validated,
        )
        video = VideoFile.objects.get(pk=video_id)
        outside_intervals = _merge_outside_frame_intervals(
            video,
            only_validated=run_config.only_validated,
        )
        has_applicable_outside_segments = bool(outside_intervals)
        rebuild_outside_intervals = (
            outside_intervals if has_applicable_outside_segments else None
        )
        mark_post_validation_incomplete(video)
        rebuilt = bool(
            rebuild_processed_video_without_outside_frames(
                video,
                only_validated=run_config.only_validated,
                outside_intervals=rebuild_outside_intervals,
            )
        )
        if not rebuilt:
            mark_post_validation_incomplete(video)
            rollback_video_frame_artifacts(
                video,
                reason="Post-validation rebuild returned false.",
            )
            if history is not None:
                reason = "Outside-frame blackening rebuild returned false."
                history.mark_failure(reason)
                fail_deferred_temporal_inference_for_rebuild(
                    video_id=video.pk,
                    rebuild_history_id=history.pk,
                    reason=(
                        "Temporal inference was not queued because the required "
                        f"frame rebuild failed: {reason}"
                    ),
                )
            return False

        video.refresh_from_db()
        _verify_processed_video_contract(
            video,
            only_validated=run_config.only_validated,
            outside_intervals=rebuild_outside_intervals,
        )
        if has_applicable_outside_segments:
            frames_extracted = extract_video_frames(
                video,
                overwrite=True,
                from_processed=True,
            )
            if not frames_extracted:
                raise RuntimeError(
                    "Post-validation rebuild could not extract processed frames "
                    f"for video {video.pk}."
                )
            _verify_extracted_frame_contract(video)
            frames_blackened = censor_outside_video_frames(
                video,
                only_validated=run_config.only_validated,
            )
            if not frames_blackened:
                raise RuntimeError(
                    "Post-validation rebuild could not blacken every outside frame "
                    f"for video {video.pk}."
                )
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
                details=(
                    "Outside-frame blackening rebuild completed."
                    if has_applicable_outside_segments
                    else "Outside-frame blackening rebuild completed with no applicable intervals."
                ),
            )
            try:
                dispatch_deferred_temporal_inference_after_rebuild(
                    video_id=video.pk,
                    rebuild_history_id=history.pk,
                )
            except Exception:
                logger.exception(
                    "Failed to dispatch deferred temporal inference after "
                    "post-validation rebuild for video %s.",
                    video.pk,
                )
        return True
    except MediaOperationDeferred:
        raise
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
            fail_deferred_temporal_inference_for_rebuild(
                video_id=video_id,
                rebuild_history_id=history.pk,
                reason=(
                    "Temporal inference was not queued because the required "
                    f"frame rebuild failed: {exc}"
                ),
            )
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
    ffmpeg_media_queue = get_celery_ffmpeg_media_queue()
    video = VideoFile.objects.get(pk=video_id)
    history, reservation_status = _reserve_blackening_history(
        video=video,
        task_id=task_id,
        only_validated=only_validated,
        queue=ffmpeg_media_queue,
    )

    if reservation_status == RESERVATION_BUSY:
        return _job_dispatch_result(
            task_id=history.task_id or "",
            mode=mode,
            status="busy",
            video_id=int(video_id),
            history_id=history.pk,
        )

    if reservation_status == RESERVATION_ALREADY_QUEUED:
        return _job_dispatch_result(
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
        return _job_dispatch_result(
            task_id=task_id,
            mode=mode,
            status="completed" if rebuilt else "failed",
            video_id=int(video_id),
            history_id=history.pk,
        )

    if mode == "celery":
        try:
            from endoreg_db.tasks import run_video_post_validation_rebuild_task

            _ensure_ffmpeg_media_broker_transport_allowed()
            countdown = get_video_post_validation_dispatch_delay_seconds()
            async_result = run_video_post_validation_rebuild_task.apply_async(
                args=(int(video_id),),
                kwargs={
                    "only_validated": bool(only_validated),
                    "history_id": history.pk,
                },
                queue=ffmpeg_media_queue,
                routing_key=ffmpeg_media_queue,
                countdown=countdown,
            )
            _set_history_task_id(history, str(async_result.id))
            return _job_dispatch_result(
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
            return _job_dispatch_result(
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
    return _job_dispatch_result(
        task_id=task_id,
        mode=mode,
        status="queued",
        video_id=int(video_id),
        history_id=history.pk,
    )
