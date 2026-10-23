"""AAA regression checks for observed timeout and integrity failure states."""

from datetime import timedelta

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from django.utils import timezone
from rapidocr.inference_engine.onnxruntime.main import ONNXRuntimeError

from endoreg_db.models import UploadJob
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
    locked_upload_job_import_lease,
    release_upload_job_import_lease,
)
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_error,
    mark_upload_job_integrity_lost,
    mark_upload_job_processing,
    schedule_upload_job_retry,
)
from endoreg_db.services.jobs.timeouts import is_processing_timeout


def test_rapidocr_wrapped_soft_timeout_is_classified_from_its_cause() -> None:
    # Arrange: the actual exception class from the failed video 15 reimport.
    timeout = SoftTimeLimitExceeded()
    wrapped = ONNXRuntimeError("OCR inference interrupted")
    wrapped.__cause__ = timeout

    # Act
    classified = is_processing_timeout(wrapped)

    # Assert: ordinary OCR failures and timeout-looking text stay distinct.
    assert classified is True
    assert is_processing_timeout(ONNXRuntimeError("SoftTimeLimitExceeded")) is False


@pytest.mark.django_db
def test_timeout_can_enter_bounded_retry_without_releasing_source_for_cleanup() -> None:
    # Arrange: a persisted source handle, with no filesystem mutation.
    job = UploadJob.objects.create(
        file="upload_watcher/recovery.mp4",
        content_type="video/mp4",
        content_hash="b" * 64,
        retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
    )
    mark_upload_job_processing(job)
    mark_upload_job_error(job, "processing_timeout")
    before = timezone.now()

    # Act: an explicit recovery decision uses the existing bounded transition.
    scheduled = schedule_upload_job_retry(
        job,
        "processing_timeout",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        delay_seconds=60,
        max_retries=1,
    )
    job.refresh_from_db()

    # Assert
    assert scheduled is True
    assert job.status == UploadJob.Status.RETRYING
    assert job.retry_count == 1
    assert job.retryable is True
    assert job.next_retry_at is not None
    assert job.next_retry_at >= before + timedelta(seconds=60)
    assert job.file.name == "upload_watcher/recovery.mp4"
    assert job.content_hash == "b" * 64
    assert job.cleanup_status == UploadJob.CleanupStatus.PENDING
    assert job.source_file_delete_eligible_at is None

    # Act / Assert: another failure exhausts the budget without claiming success.
    mark_upload_job_processing(job)
    assert (
        schedule_upload_job_retry(
            job,
            "processing_timeout",
            error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
            delay_seconds=60,
        )
        is False
    )
    job.refresh_from_db()
    assert job.status == UploadJob.Status.ERROR
    assert job.retryable is False
    assert job.next_retry_at is None
    assert job.retry_count == 1
    assert job.source_file_delete_eligible_at is None


@pytest.mark.django_db
def test_deleted_media_integrity_loss_cannot_be_retried_as_transient_failure() -> None:
    # Arrange
    job = UploadJob.objects.create(
        file="upload_watcher/deleted-media.mp4", content_type="video/mp4"
    )
    mark_upload_job_processing(job)
    mark_upload_job_integrity_lost(
        job,
        "Associated media record was deleted",
        error_code=UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED,
    )

    # Act / Assert: recovery requires reconciliation, not blind rescheduling.
    with pytest.raises(ValueError, match="invalid UploadJob status transition"):
        schedule_upload_job_retry(
            job,
            "retry requested",
            error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
            delay_seconds=60,
        )
    job.refresh_from_db()
    assert job.status == UploadJob.Status.LOST
    assert job.error_code == UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED
    assert job.retry_count == 0
    assert job.next_retry_at is None
    assert job.source_file_delete_eligible_at is None


@pytest.mark.django_db
def test_expired_import_attempt_is_fenced_after_recovery_claim() -> None:
    # Arrange
    job = UploadJob.objects.create(
        file="upload_watcher/fenced-recovery.mp4", content_type="video/mp4"
    )
    first = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="first-attempt"
    )
    mark_upload_job_processing(job)
    UploadJob.objects.filter(pk=job.pk).update(
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )

    # Act
    replacement = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="recovery-attempt"
    )

    # Assert: neither late publication nor release by the old worker is allowed.
    assert replacement.fencing_epoch == first.fencing_epoch + 1
    with pytest.raises(UploadJobImportLeaseLost):
        with locked_upload_job_import_lease(first):
            pytest.fail("stale worker entered the mutation boundary")
    with pytest.raises(UploadJobImportLeaseLost):
        release_upload_job_import_lease(first)
    job.refresh_from_db()
    assert job.processing_lease_owner == replacement.owner
    release_upload_job_import_lease(replacement)
