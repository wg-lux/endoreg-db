from __future__ import annotations

from collections.abc import Callable
import logging
from typing import NoReturn

from endoreg_db.config.env import get_video_post_validation_dispatch_delay_seconds
from endoreg_db.exceptions import MediaOperationDeferred, describe_job_error
from endoreg_db.utils.structured_logging import emit_structured_event, hash_identifier


logger = logging.getLogger("endoreg_db.jobs")

RetryCallable = Callable[..., BaseException]


def retry_deferred_media_operation(
    *,
    retry: RetryCallable,
    error: MediaOperationDeferred,
    job_name: str,
    video_id: object,
) -> NoReturn:
    """Apply the shared Celery retry and audit policy for deferred media work."""

    descriptor = describe_job_error(error)
    if not descriptor.retryable:
        raise error
    countdown = max(
        get_video_post_validation_dispatch_delay_seconds(),
        descriptor.minimum_countdown_seconds,
    )
    emit_structured_event(
        logger,
        "job.retry_scheduled",
        job_name=job_name,
        subject_id_sha256=hash_identifier(video_id),
        error_code=descriptor.code.value,
        reason=descriptor.log_reason,
        retryable=descriptor.retryable,
        countdown_seconds=countdown,
        max_retries=descriptor.max_retries,
    )
    raise retry(
        exc=error,
        countdown=countdown,
        max_retries=descriptor.max_retries,
    ) from error


__all__ = ["retry_deferred_media_operation"]
