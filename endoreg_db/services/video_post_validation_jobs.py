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
    return bool(
        VideoFile.create_video_without_outside_frames(
            video, only_validated=only_validated
        )
    )


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
