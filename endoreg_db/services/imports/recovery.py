"""Rediscover persisted import stacks without trusting broker delivery state."""

import logging
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Now
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.schemas.import_processing import ImportProcessingStack
from endoreg_db.services.hub.upload_job_state_machine import (
    admit_upload_job_recovery,
    mark_upload_job_error,
)
from endoreg_db.services.reports.import_fencing import (
    acquire_report_import_fence,
    mark_report_import_fence_failed,
)
from endoreg_db.utils.storage import file_exists
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


def recover_persisted_import_stacks(*, limit: int = 100) -> int:
    if limit < 1:
        raise ValueError("limit must be positive")
    eligible = (
        UploadJob.objects.filter(
            processing_stack__isnull=False,
            source_center__isnull=False,
            source_file_persisted=True,
            cancellation_requested_at__isnull=True,
            overview_dismissed_at__isnull=True,
            processing_lease_owner="",
            processing_lease_expires_at__isnull=True,
            cleanup_status__in=[
                UploadJob.CleanupStatus.PENDING,
                UploadJob.CleanupStatus.SKIPPED,
            ],
            retry_count__lt=F("max_retries"),
        )
        .exclude(file="")
        .exclude(storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED)
    )
    from endoreg_db.services.hub.import_monitoring import RETRY_HANDOFF_TIMEOUT_SECONDS

    reports = eligible.filter(
        status=UploadJob.Status.PROCESSING,
        content_type__in=["application/pdf", "text/plain"],
        last_attempt_at__lte=timezone.now()
        - timedelta(seconds=RETRY_HANDOFF_TIMEOUT_SECONDS),
    )
    # Lock order matches report publication: content attempt, then upload job.
    report_ids = tuple(
        reports.order_by("updated_at", "pk").values_list("pk", flat=True)[:limit]
    )
    for job_id in report_ids:
        snapshot = reports.filter(pk=job_id).first()
        if snapshot is None:
            continue
        source_hash = ImportProcessingStack.model_validate(
            snapshot.processing_stack
        ).source_sha256
        with transaction.atomic():
            if source_hash is not None:
                attempt = (
                    ReportImportAttempt.objects.select_for_update(skip_locked=True)
                    .filter(content_hash=source_hash)
                    .exclude(
                        status=ReportImportAttempt.STATUS_ACTIVE,
                        lease_expires_at__gt=Now(),
                    )
                    .first()
                )
                if attempt is None:
                    continue
            job = reports.select_for_update().filter(pk=job_id).first()
            if job is None or not file_exists(job.file):
                continue
            stack = ImportProcessingStack.model_validate(job.processing_stack)
            if not stack.remaining or stack.source_sha256 != source_hash:
                continue
            if source_hash is not None:
                fence = acquire_report_import_fence(source_hash)
                if not mark_report_import_fence_failed(fence):
                    raise RuntimeError("Recovered report fence could not be released")
            mark_upload_job_error(
                job,
                "Report import handoff or worker expired; retained processing stack will resume.",
                error_code=UploadJob.ErrorCode.IMPORT_INTERRUPTED.value,
            )

    interrupted = eligible.filter(
        status=UploadJob.Status.ERROR, error_code=UploadJob.ErrorCode.IMPORT_INTERRUPTED
    )
    identifiers = tuple(
        interrupted.order_by("updated_at", "pk").values_list("pk", flat=True)[:limit]
    )
    recovered = 0
    from endoreg_db.services.hub.import_monitoring import retry_delay_seconds

    for job_id in identifiers:
        with transaction.atomic():
            job = interrupted.select_for_update().filter(pk=job_id).first()
            if job is None or not file_exists(job.file):
                continue
            stack = ImportProcessingStack.model_validate(job.processing_stack)
            if not stack.remaining:
                continue
            if admit_upload_job_recovery(
                job,
                "Resuming persisted Rust import stack after worker restart.",
                delay_seconds=retry_delay_seconds(job.retry_count),
            ):
                recovered += 1
                emit_structured_event(
                    logger,
                    "import.stack_recovery_queued",
                    upload_job_id=str(job.pk),
                    checkpoint_reference=str(stack.reference),
                )
    return recovered
