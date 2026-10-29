from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.anonymization import (
    AnonymizationPhase,
    AnonymizationState,
    anonymization_phase,
)
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_processing,
    schedule_upload_job_retry,
)
from endoreg_db.utils.structured_logging import emit_structured_event

import logging


logger = logging.getLogger(__name__)

DEFAULT_RETRY_DELAY_SECONDS = 30
MAX_RETRY_DELAY_SECONDS = 15 * 60
RETRY_HANDOFF_TIMEOUT_SECONDS = 60
STORAGE_RETRY_MAX_RETRIES = 96
INSUFFICIENT_STORAGE_ERROR_PREFIX = "Insufficient pipeline storage."


def video_import_monitoring_status(
    video: VideoFile, *, upload_job: UploadJob | None
) -> AnonymizationState:
    """Project an interrupted attempt without rewriting ambiguous media state.

    Streaming backfill can finish after the import owner dies; it does not
    commit import completion. A newer media state or completed publication
    must not be replaced by an older interrupted upload's diagnostics.
    """
    state = video.state
    status = state.anonymization_status if state else AnonymizationState.NOT_STARTED
    if (
        state is not None
        and anonymization_phase(status) is AnonymizationPhase.IN_PROGRESS
        and upload_job is not None
        and upload_job.status == UploadJob.Status.ERROR
        and upload_job.error_code == UploadJob.ErrorCode.IMPORT_INTERRUPTED
        and not upload_job.processing_lease_owner
        and (upload_job.source_center.pk if upload_job.source_center else None)
        == video.center_id
        and bool(video.raw_video_hash)
        and upload_job.content_hash == video.raw_video_hash
        and upload_job.content_type.startswith("video/")
        and upload_job.updated_at >= state.date_modified
    ):
        return AnonymizationState.FAILED
    return status


def latest_video_import_job(video: VideoFile) -> UploadJob | None:
    """Use the overview's source/center scope and newest-attempt ordering."""
    if not video.raw_video_hash:
        return None
    return (
        UploadJob.objects.select_related("source_center")
        .filter(
            source_center_id=video.center_id,
            content_hash=video.raw_video_hash,
            content_type__startswith="video/",
        )
        .exclude(dismissed_upload_job_filter())
        .order_by("-updated_at", "-created_at")
        .first()
    )


def can_dismiss_upload_job(job: UploadJob) -> bool:
    return (
        job.status in {UploadJob.Status.ERROR, UploadJob.Status.LOST}
        and not job.processing_lease_owner
        and job.cleanup_status != UploadJob.CleanupStatus.DELETING
    )


def dismissed_upload_job_filter() -> Q:
    """Dismiss only the reviewed failure; subsequent processing stays observable."""
    return (
        Q(status__in=[UploadJob.Status.ERROR, UploadJob.Status.LOST])
        & Q(overview_dismissed_at__isnull=False)
        & (
            Q(last_attempt_at__isnull=True)
            | Q(last_attempt_at__lte=F("overview_dismissed_at"))
        )
    )


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
        UploadJob.ErrorCode.NONE.value: "",
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
        UploadJob.ErrorCode.IMPORT_INTERRUPTED.value: (
            "Import execution was interrupted. Retained media requires operator review "
            "before recovery."
        ),
        UploadJob.ErrorCode.SOURCE_MISSING.value: (
            "The import source is missing or inconsistent. A safe re-import is required."
        ),
    }
    try:
        return messages[error_code]
    except KeyError:
        raise ValueError("Unsupported import error code") from None


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
    from endoreg_db.services.hub.upload_job_import_lease import (
        reconcile_expired_upload_job_import_leases,
        recover_interrupted_upload_jobs,
    )

    reconcile_expired_upload_job_import_leases(limit=limit)
    from endoreg_db.services.imports.recovery import recover_persisted_import_stacks

    recover_persisted_import_stacks(limit=limit)
    recover_interrupted_upload_jobs(limit=limit)
    due_jobs = UploadJob.objects.filter(
        status=UploadJob.Status.RETRYING,
        retryable=True,
        next_retry_at__lte=timezone.now(),
    ).filter(
        Q(last_attempt_at__isnull=True)
        | Q(last_attempt_at__lt=F("next_retry_at"))
        | Q(
            last_attempt_at__lte=timezone.now()
            - timedelta(seconds=RETRY_HANDOFF_TIMEOUT_SECONDS)
        )
    )
    due_ids = tuple(
        due_jobs.order_by(
            F("processing_stack__priority").desc(nulls_last=True),
            "next_retry_at",
            "created_at",
        ).values_list("pk", flat=True)[:limit]
    )
    dispatched_count = 0
    failed_count = 0
    for upload_job_id in due_ids:
        with transaction.atomic():
            upload_job = due_jobs.select_for_update().filter(pk=upload_job_id).first()
            if upload_job is None:
                continue
            if upload_job.content_type.startswith("video/"):
                # A broker handoff is not worker ownership. Keep rediscovery
                # possible if the process dies before sending or delivery is lost.
                upload_job.last_attempt_at = timezone.now()
                upload_job.save(update_fields=["last_attempt_at", "updated_at"])
            else:
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
