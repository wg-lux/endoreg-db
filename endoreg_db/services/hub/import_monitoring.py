from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from django.db import transaction
from django.utils import timezone

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_processing,
    schedule_upload_job_retry,
)
from endoreg_db.utils.structured_logging import emit_structured_event

import logging


logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAY_SECONDS = 30
MAX_RETRY_DELAY_SECONDS = 15 * 60
STORAGE_RETRY_MAX_RETRIES = 96
INSUFFICIENT_STORAGE_ERROR_PREFIX = "Insufficient pipeline storage."


class UploadJobRetryDispatcher(Protocol):
    def apply_async(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class UploadJobRetryDispatchResult:
    due_count: int
    dispatched_count: int
    failed_count: int


def safe_import_error_detail(error_code: str) -> str:
    """Return a stable operator-safe message without exception or path details."""
    messages = {
        UploadJob.ErrorCode.DISPATCH_UNAVAILABLE.value: (
            "Import service is temporarily unavailable. An automatic retry is scheduled."
        ),
        UploadJob.ErrorCode.DUPLICATE_CONTENT.value: (
            "Duplicate content was detected. Existing validated data remains unchanged."
        ),
        UploadJob.ErrorCode.INVALID_CONFIGURATION.value: (
            "Import configuration is incomplete. Operator action is required."
        ),
        UploadJob.ErrorCode.INVALID_INPUT.value: (
            "The source does not satisfy the supported import contract."
        ),
        UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED.value: (
            "Media integrity validation failed. The source requires review."
        ),
        UploadJob.ErrorCode.PROCESSING_FAILED.value: (
            "Import processing failed. Technical details are available in protected logs."
        ),
        UploadJob.ErrorCode.SOURCE_MISSING.value: (
            "The import source is missing or inconsistent. A safe re-import is required."
        ),
    }
    return messages.get(error_code, "")


def retry_delay_seconds(retry_count: int) -> int:
    if retry_count < 0:
        raise ValueError("retry_count must not be negative")
    return min(DEFAULT_RETRY_DELAY_SECONDS * (2**retry_count), MAX_RETRY_DELAY_SECONDS)


def schedule_dispatch_retry(upload_job: UploadJob, *, technical_detail: str) -> bool:
    delay_seconds = retry_delay_seconds(upload_job.retry_count)
    scheduled = schedule_upload_job_retry(
        upload_job,
        technical_detail,
        error_code=UploadJob.ErrorCode.DISPATCH_UNAVAILABLE.value,
        delay_seconds=delay_seconds,
    )
    emit_structured_event(
        logger,
        "import.retry_scheduled" if scheduled else "import.retry_exhausted",
        level=logging.WARNING if scheduled else logging.ERROR,
        upload_job_id=str(upload_job.pk),
        error_code=UploadJob.ErrorCode.DISPATCH_UNAVAILABLE.value,
        retryable=scheduled,
        retry_count=upload_job.retry_count,
        max_retries=upload_job.max_retries,
        next_retry_at=(
            upload_job.next_retry_at.isoformat()
            if upload_job.next_retry_at is not None
            else None
        ),
    )
    return scheduled


def is_retryable_storage_failure(upload_job: UploadJob) -> bool:
    """Recognize legacy terminal jobs created before storage retries existed."""
    return (
        upload_job.status == UploadJob.Status.ERROR.value
        and upload_job.error_code == UploadJob.ErrorCode.PROCESSING_FAILED.value
        and upload_job.source_file_persisted
        and bool(upload_job.file and upload_job.file.name)
        and upload_job.error_detail.startswith(INSUFFICIENT_STORAGE_ERROR_PREFIX)
    )


def schedule_storage_retry(upload_job: UploadJob, *, technical_detail: str) -> bool:
    """Retain a managed source and schedule bounded recovery from disk pressure."""
    delay_seconds = retry_delay_seconds(upload_job.retry_count)
    scheduled = schedule_upload_job_retry(
        upload_job,
        technical_detail,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED.value,
        delay_seconds=delay_seconds,
        max_retries=max(upload_job.max_retries, STORAGE_RETRY_MAX_RETRIES),
    )
    emit_structured_event(
        logger,
        "import.storage_retry_scheduled"
        if scheduled
        else "import.storage_retry_exhausted",
        level=logging.WARNING if scheduled else logging.ERROR,
        upload_job_id=str(upload_job.pk),
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED.value,
        retryable=scheduled,
        retry_count=upload_job.retry_count,
        max_retries=upload_job.max_retries,
        next_retry_at=(
            upload_job.next_retry_at.isoformat()
            if upload_job.next_retry_at is not None
            else None
        ),
    )
    return scheduled


def schedule_processing_retry(upload_job: UploadJob, *, technical_detail: str) -> bool:
    """Retain a managed source and schedule bounded recovery from processing faults."""
    delay_seconds = retry_delay_seconds(upload_job.retry_count)
    scheduled = schedule_upload_job_retry(
        upload_job,
        technical_detail,
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED.value,
        delay_seconds=delay_seconds,
    )
    emit_structured_event(
        logger,
        "import.processing_retry_scheduled"
        if scheduled
        else "import.processing_retry_exhausted",
        level=logging.WARNING if scheduled else logging.ERROR,
        upload_job_id=str(upload_job.pk),
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED.value,
        retryable=scheduled,
        retry_count=upload_job.retry_count,
        max_retries=upload_job.max_retries,
        next_retry_at=(
            upload_job.next_retry_at.isoformat()
            if upload_job.next_retry_at is not None
            else None
        ),
    )
    return scheduled


def dispatch_due_upload_job_retries(
    *,
    dispatcher: UploadJobRetryDispatcher,
    queue: str,
    limit: int = 100,
) -> UploadJobRetryDispatchResult:
    if limit < 1:
        raise ValueError("limit must be positive")
    due_ids = tuple(
        UploadJob.objects.filter(
            status=UploadJob.Status.RETRYING,
            retryable=True,
            next_retry_at__lte=timezone.now(),
        )
        .order_by("next_retry_at", "created_at")
        .values_list("pk", flat=True)[:limit]
    )
    dispatched_count = 0
    failed_count = 0
    for upload_job_id in due_ids:
        with transaction.atomic():
            upload_job = UploadJob.objects.select_for_update().get(pk=upload_job_id)
            if (
                upload_job.status != UploadJob.Status.RETRYING.value
                or not upload_job.retryable
                or upload_job.next_retry_at is None
                or upload_job.next_retry_at > timezone.now()
            ):
                continue
            mark_upload_job_processing(upload_job)
        try:
            dispatcher.apply_async(
                args=(str(upload_job.pk),),
                queue=queue,
                routing_key=queue,
            )
        except Exception as exc:
            failed_count += 1
            schedule_dispatch_retry(
                upload_job,
                technical_detail=f"Retry dispatch failed: {exc}",
            )
            continue
        dispatched_count += 1
        emit_structured_event(
            logger,
            "import.retry_dispatched",
            upload_job_id=str(upload_job.pk),
            retry_count=upload_job.retry_count,
            queue=queue,
        )
    return UploadJobRetryDispatchResult(
        due_count=len(due_ids),
        dispatched_count=dispatched_count,
        failed_count=failed_count,
    )


__all__ = [
    "UploadJobRetryDispatchResult",
    "dispatch_due_upload_job_retries",
    "is_retryable_storage_failure",
    "retry_delay_seconds",
    "safe_import_error_detail",
    "schedule_dispatch_retry",
    "schedule_processing_retry",
    "schedule_storage_retry",
]
