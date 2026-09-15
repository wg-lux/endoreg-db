from __future__ import annotations

import pytest

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.upload_job_state_machine import (
    validate_upload_job_interrupted_retry,
    validate_upload_job_status_transition,
)


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
