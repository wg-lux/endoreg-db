from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict

from endoreg_db.config.env import (
    get_video_post_validation_job_max_workers,
    get_video_post_validation_job_mode,
)

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=get_video_post_validation_job_max_workers())


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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _run_video_post_validation_rebuild(
    video_id: int, *, only_validated: bool = False
) -> bool:
    from endoreg_db.models import VideoFile

    video = VideoFile.objects.get(pk=video_id)
    rebuilt = bool(
        VideoFile.create_video_without_outside_frames(
            video, only_validated=only_validated
        )
    )
    if rebuilt:
        video.refresh_from_db()
        _verify_extracted_frame_contract(video)
    return rebuilt


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

    if mode == "inline":
        _run_video_post_validation_rebuild(video_id, only_validated=only_validated)
        return JobDispatchResult(
            task_id=task_id,
            mode=mode,
            status="completed",
            video_id=int(video_id),
        )

    if mode == "celery":
        try:
            from endoreg_db.tasks import run_video_post_validation_rebuild_task

            async_result = run_video_post_validation_rebuild_task.delay(
                int(video_id), only_validated=bool(only_validated)
            )
            return JobDispatchResult(
                task_id=str(async_result.id),
                mode=mode,
                status="queued",
                video_id=int(video_id),
            )
        except Exception:
            logger.exception(
                "Celery dispatch failed for video %s. Falling back to thread mode.",
                video_id,
            )
            mode = "thread"

    def _job():
        try:
            _run_video_post_validation_rebuild(video_id, only_validated=only_validated)
        except Exception:
            logger.exception(
                "Async post-validation rebuild failed for video %s (task_id=%s)",
                video_id,
                task_id,
            )

    _executor.submit(_job)
    return JobDispatchResult(
        task_id=task_id,
        mode=mode,
        status="queued",
        video_id=int(video_id),
    )
