from __future__ import annotations

import pytest

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.upload_job_state_machine import (
    admit_upload_job_recovery,
    validate_upload_job_interrupted_retry,
    validate_upload_job_interruption,
    validate_upload_job_status_transition,
)


@pytest.mark.django_db
@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize(
    "error_code",
    [UploadJob.ErrorCode.IMPORT_INTERRUPTED, UploadJob.ErrorCode.PROCESSING_FAILED],
)
def test_recovery_admission_preserves_retry_cap(
    exhausted: bool, error_code: UploadJob.ErrorCode
) -> None:
    job = UploadJob.objects.create(
        status=UploadJob.Status.ERROR,
        error_code=error_code,
        retry_count=3 if exhausted else 0,
        max_retries=3,
    )
    assert admit_upload_job_recovery(job, "recover", delay_seconds=1) is (not exhausted)
    job.refresh_from_db()
    assert job.max_retries == 3
    assert job.retry_count == (3 if exhausted else 1)
    assert job.status == (
        UploadJob.Status.ERROR if exhausted else UploadJob.Status.RETRYING
    )


@pytest.mark.django_db
def test_recovery_admission_rejects_a_superseded_snapshot() -> None:
    from endoreg_db.services.hub.upload_job_import_lease import UploadJobImportLeaseLost

    job = UploadJob.objects.create(
        status=UploadJob.Status.ERROR,
        error_code=UploadJob.ErrorCode.IMPORT_INTERRUPTED,
    )
    UploadJob.objects.filter(pk=job.pk).update(processing_fencing_token=1)
    with pytest.raises(UploadJobImportLeaseLost, match="superseded"):
        admit_upload_job_recovery(job, "recover", delay_seconds=1)
    job.refresh_from_db()
    assert job.status == UploadJob.Status.ERROR
    assert job.retry_count == 0
    assert job.max_retries == 3


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (UploadJob.Status.PENDING.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.RETRYING.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.ERROR.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.ANONYMIZED.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.PENDING.value, UploadJob.Status.RETRYING.value),
        (UploadJob.Status.PROCESSING.value, UploadJob.Status.RETRYING.value),
        (UploadJob.Status.ERROR.value, UploadJob.Status.RETRYING.value),
        (UploadJob.Status.PROCESSING.value, UploadJob.Status.ANONYMIZED.value),
        (UploadJob.Status.PENDING.value, UploadJob.Status.ERROR.value),
        (UploadJob.Status.PROCESSING.value, UploadJob.Status.ERROR.value),
        (UploadJob.Status.RETRYING.value, UploadJob.Status.ERROR.value),
        (UploadJob.Status.ANONYMIZED.value, UploadJob.Status.ERROR.value),
        (UploadJob.Status.PENDING.value, UploadJob.Status.LOST.value),
        (UploadJob.Status.PROCESSING.value, UploadJob.Status.LOST.value),
        (UploadJob.Status.RETRYING.value, UploadJob.Status.LOST.value),
        (UploadJob.Status.ANONYMIZED.value, UploadJob.Status.LOST.value),
    ],
)
def test_upload_job_status_transition_uses_native_operation_reducer(
    current_status: str,
    target_status: str,
) -> None:
    validate_upload_job_status_transition(
        current_status=current_status,
        target_status=target_status,
    )


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (UploadJob.Status.LOST.value, UploadJob.Status.ANONYMIZED.value),
        (UploadJob.Status.LOST.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.PENDING.value, UploadJob.Status.ANONYMIZED.value),
    ],
)
def test_upload_job_status_transition_rejects_terminal_or_skipped_paths(
    current_status: str,
    target_status: str,
) -> None:
    with pytest.raises(ValueError, match="invalid UploadJob status transition"):
        validate_upload_job_status_transition(
            current_status=current_status,
            target_status=target_status,
        )


def test_upload_job_status_transition_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown UploadJob status"):
        validate_upload_job_status_transition(
            current_status="invented",
            target_status=UploadJob.Status.ERROR.value,
        )


@pytest.mark.parametrize(
    "current_status",
    [
        UploadJob.Status.PENDING.value,
        UploadJob.Status.PROCESSING.value,
        UploadJob.Status.RETRYING.value,
    ],
)
def test_upload_job_interrupted_retry_recovers_to_running(
    current_status: str,
) -> None:
    validate_upload_job_interrupted_retry(current_status=current_status)


@pytest.mark.parametrize(
    "current_status",
    [UploadJob.Status.ANONYMIZED.value, UploadJob.Status.ERROR.value],
)
def test_upload_job_terminal_outcome_is_not_an_interrupted_attempt(
    current_status: str,
) -> None:
    with pytest.raises(ValueError, match="invalid operation lifecycle transition"):
        validate_upload_job_interrupted_retry(current_status=current_status)


@pytest.mark.parametrize("current_status", list(UploadJob.Status) + ["invented"])
def test_upload_job_interruption_requires_active_execution(current_status: str) -> None:
    if current_status in (
        UploadJob.Status.PROCESSING,
        UploadJob.Status.CANCEL_REQUESTED,
    ):
        validate_upload_job_interruption(current_status=current_status)
    else:
        with pytest.raises(ValueError, match="no active execution to interrupt"):
            validate_upload_job_interruption(current_status=current_status)


@pytest.mark.parametrize("status", list(UploadJob.Status))
def test_every_upload_job_status_reduces_through_the_native_lifecycle(
    status: UploadJob.Status,
) -> None:
    validate_upload_job_status_transition(
        current_status=status.value, target_status=status.value
    )


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (UploadJob.Status.PENDING.value, UploadJob.Status.CANCELLED.value),
        (UploadJob.Status.RETRYING.value, UploadJob.Status.CANCELLED.value),
        (UploadJob.Status.PROCESSING.value, UploadJob.Status.CANCELLED.value),
        (UploadJob.Status.PROCESSING.value, UploadJob.Status.CANCEL_REQUESTED.value),
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.CANCELLED.value),
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.ERROR.value),
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.LOST.value),
    ],
)
def test_cancellation_reduces_through_native_cancel(
    current_status: str, target_status: str
) -> None:
    validate_upload_job_status_transition(
        current_status=current_status, target_status=target_status
    )


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.RETRYING.value),
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.PENDING.value),
        (UploadJob.Status.CANCEL_REQUESTED.value, UploadJob.Status.ANONYMIZED.value),
        (UploadJob.Status.CANCELLED.value, UploadJob.Status.PROCESSING.value),
        (UploadJob.Status.CANCELLED.value, UploadJob.Status.RETRYING.value),
        (UploadJob.Status.CANCELLED.value, UploadJob.Status.ERROR.value),
        (UploadJob.Status.ANONYMIZED.value, UploadJob.Status.CANCELLED.value),
        (UploadJob.Status.ERROR.value, UploadJob.Status.CANCELLED.value),
    ],
)
def test_cancellation_intent_and_outcome_are_irreversible(
    current_status: str, target_status: str
) -> None:
    with pytest.raises(ValueError, match="invalid UploadJob status transition"):
        validate_upload_job_status_transition(
            current_status=current_status, target_status=target_status
        )
