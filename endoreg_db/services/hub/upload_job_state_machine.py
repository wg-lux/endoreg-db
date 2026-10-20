from __future__ import annotations

from collections.abc import Iterable, Sequence

from django.db import transaction

from endoreg_db.models.hub.upload_job import UploadJob, UploadJobSensitiveMeta
from endoreg_db.services.runtime.lifecycle_state_machine import (
    OperationClaimPath,
    OperationLifecycleEvent,
    OperationLifecycleState,
    reduce_operation_lifecycle,
)


_UPLOAD_JOB_STATES: dict[str, OperationLifecycleState] = {
    UploadJob.Status.PENDING.value: OperationLifecycleState.QUEUED,
    UploadJob.Status.PROCESSING.value: OperationLifecycleState.RUNNING,
    UploadJob.Status.RETRYING.value: OperationLifecycleState.RETRY_WAIT,
    UploadJob.Status.ANONYMIZED.value: OperationLifecycleState.SUCCEEDED,
    UploadJob.Status.ERROR.value: OperationLifecycleState.FAILED,
    UploadJob.Status.LOST.value: OperationLifecycleState.LOST,
}

_UPLOAD_JOB_EVENTS: dict[
    tuple[OperationLifecycleState, OperationLifecycleState],
    Sequence[OperationLifecycleEvent],
] = {
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleState.RUNNING,
    ): OperationClaimPath.INITIAL.events,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleState.RUNNING,
    ): OperationClaimPath.RETRY_READY.events,
    (OperationLifecycleState.RUNNING, OperationLifecycleState.RETRY_WAIT): (
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ),
    (OperationLifecycleState.QUEUED, OperationLifecycleState.RETRY_WAIT): (
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ),
    (OperationLifecycleState.FAILED, OperationLifecycleState.RETRY_WAIT): (
        OperationLifecycleEvent.RETRY_REQUESTED,
    ),
    (
        OperationLifecycleState.FAILED,
        OperationLifecycleState.RUNNING,
    ): OperationClaimPath.RETRY_REQUESTED.events,
    (
        OperationLifecycleState.SUCCEEDED,
        OperationLifecycleState.RUNNING,
    ): OperationClaimPath.RETRY_REQUESTED.events,
    (OperationLifecycleState.SUCCEEDED, OperationLifecycleState.FAILED): (
        *OperationClaimPath.RETRY_REQUESTED.events,
        OperationLifecycleEvent.FAIL,
    ),
    (OperationLifecycleState.RUNNING, OperationLifecycleState.SUCCEEDED): (
        OperationLifecycleEvent.SUCCEED,
    ),
    (OperationLifecycleState.QUEUED, OperationLifecycleState.FAILED): (
        OperationLifecycleEvent.FAIL,
    ),
    (OperationLifecycleState.RUNNING, OperationLifecycleState.FAILED): (
        OperationLifecycleEvent.FAIL,
    ),
    (OperationLifecycleState.RETRY_WAIT, OperationLifecycleState.FAILED): (
        OperationLifecycleEvent.FAIL,
    ),
    (OperationLifecycleState.QUEUED, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.INTEGRITY_LOST,
    ),
    (OperationLifecycleState.RUNNING, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.INTEGRITY_LOST,
    ),
    (OperationLifecycleState.RETRY_WAIT, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.INTEGRITY_LOST,
    ),
    (OperationLifecycleState.SUCCEEDED, OperationLifecycleState.LOST): (
        OperationLifecycleEvent.INTEGRITY_LOST,
    ),
}

_IDEMPOTENT_EVENTS: dict[OperationLifecycleState, OperationLifecycleEvent] = {
    OperationLifecycleState.QUEUED: OperationLifecycleEvent.RETRY_READY,
    OperationLifecycleState.RUNNING: OperationLifecycleEvent.START,
    OperationLifecycleState.RETRY_WAIT: OperationLifecycleEvent.RETRY_SCHEDULED,
    OperationLifecycleState.SUCCEEDED: OperationLifecycleEvent.SUCCEED,
    OperationLifecycleState.FAILED: OperationLifecycleEvent.FAIL,
    OperationLifecycleState.LOST: OperationLifecycleEvent.INTEGRITY_LOST,
}


def validate_upload_job_status_transition(
    *,
    current_status: str,
    target_status: str,
) -> None:
    """Validate an UploadJob mutation without performing persistence effects."""
    try:
        current_state = _UPLOAD_JOB_STATES[current_status]
        target_state = _UPLOAD_JOB_STATES[target_status]
    except KeyError as exc:
        raise ValueError(f"unknown UploadJob status: {exc.args[0]}") from exc

    if current_state is target_state:
        events: Sequence[OperationLifecycleEvent] = (_IDEMPOTENT_EVENTS[current_state],)
    else:
        try:
            events = _UPLOAD_JOB_EVENTS[(current_state, target_state)]
        except KeyError as exc:
            raise ValueError(
                "invalid UploadJob status transition: "
                f"{current_status} -> {target_status}"
            ) from exc

    reduced_state = reduce_operation_lifecycle(current_state, events)
    if reduced_state is not target_state:
        raise RuntimeError(
            "native UploadJob lifecycle reduction produced unexpected state: "
            f"{reduced_state.value}"
        )


def _guard_interrupted_upload_job(upload_job: UploadJob) -> None:
    from endoreg_db.services.hub.upload_job_import_lease import (
        raise_if_upload_job_import_interrupted,
    )

    # Callers hold this lock through their transition; a stale in-memory object
    # must not erase a reconciliation decision committed by another worker.
    current = UploadJob.objects.select_for_update().get(pk=upload_job.pk)
    raise_if_upload_job_import_interrupted(current)


@transaction.atomic
def mark_upload_job_processing(upload_job: UploadJob) -> None:
    _guard_interrupted_upload_job(upload_job)
    validate_upload_job_status_transition(
        current_status=upload_job.status,
        target_status=UploadJob.Status.PROCESSING.value,
    )
    upload_job.mark_processing()


@transaction.atomic
def mark_upload_job_completed(
    upload_job: UploadJob,
    *,
    sensitive_meta: UploadJobSensitiveMeta = None,
) -> None:
    _guard_interrupted_upload_job(upload_job)
    validate_upload_job_status_transition(
        current_status=upload_job.status,
        target_status=UploadJob.Status.ANONYMIZED.value,
    )
    upload_job.mark_completed(sensitive_meta=sensitive_meta)


@transaction.atomic
def mark_upload_job_error(
    upload_job: UploadJob,
    error_detail: str,
    *,
    error_code: str = UploadJob.ErrorCode.PROCESSING_FAILED.value,
) -> None:
    _guard_interrupted_upload_job(upload_job)
    validate_upload_job_status_transition(
        current_status=upload_job.status,
        target_status=UploadJob.Status.ERROR.value,
    )
    upload_job.mark_error(error_detail, error_code=error_code)


@transaction.atomic
def mark_upload_job_integrity_lost(
    upload_job: UploadJob,
    error_detail: str,
    *,
    error_code: str = UploadJob.ErrorCode.SOURCE_MISSING.value,
) -> None:
    _guard_interrupted_upload_job(upload_job)
    validate_upload_job_status_transition(
        current_status=upload_job.status,
        target_status=UploadJob.Status.LOST.value,
    )
    upload_job.mark_lost(error_detail, error_code=error_code)


def validate_upload_job_interruption(*, current_status: str) -> None:
    """Authorize expired execution interruption through the native lifecycle."""
    if current_status not in (
        UploadJob.Status.PROCESSING.value,
        UploadJob.Status.CANCEL_REQUESTED.value,
    ):
        raise ValueError(
            f"UploadJob has no active execution to interrupt: {current_status}"
        )
    # Cancellation intent does not prove that execution or cleanup finished.
    interrupted_state = reduce_operation_lifecycle(
        OperationLifecycleState.RUNNING,
        (
            OperationLifecycleEvent.OWNERSHIP_LOST,
            OperationLifecycleEvent.RECONCILE_FAIL,
        ),
    )
    if interrupted_state is not OperationLifecycleState.FAILED:
        raise RuntimeError(
            "native UploadJob interruption reduction produced unexpected state: "
            f"{interrupted_state.value}"
        )


def validate_upload_job_interrupted_retry(*, current_status: str) -> None:
    """Validate immediate fenced recovery after an expired import owner."""
    try:
        current_state = _UPLOAD_JOB_STATES[current_status]
    except KeyError as exc:
        raise ValueError(f"unknown UploadJob status: {exc.args[0]}") from exc
    recovered_state = reduce_operation_lifecycle(
        current_state,
        OperationClaimPath.INTERRUPTED.events,
    )
    if recovered_state is not OperationLifecycleState.RUNNING:
        raise RuntimeError(
            "native UploadJob interrupted-retry reduction produced unexpected "
            f"state: {recovered_state.value}"
        )


@transaction.atomic
def schedule_upload_job_retry(
    upload_job: UploadJob,
    error_detail: str,
    *,
    error_code: str,
    delay_seconds: int,
    max_retries: int | None = None,
) -> bool:
    """Validate a bounded retry decision before the existing atomic mutation."""
    _guard_interrupted_upload_job(upload_job)
    retry_limit = upload_job.max_retries if max_retries is None else max_retries
    if retry_limit < 1:
        raise ValueError("max_retries must be positive")
    if delay_seconds < 1:
        raise ValueError("delay_seconds must be positive")
    target_status = (
        UploadJob.Status.ERROR.value
        if upload_job.retry_count >= retry_limit
        else UploadJob.Status.RETRYING.value
    )
    validate_upload_job_status_transition(
        current_status=upload_job.status,
        target_status=target_status,
    )
    return upload_job.schedule_retry(
        error_detail,
        error_code=error_code,
        delay_seconds=delay_seconds,
        max_retries=max_retries,
    )


@transaction.atomic
def transition_reimport_upload_jobs(
    jobs: Iterable[UploadJob],
    *,
    status: UploadJob.Status,
    error_detail: str = "",
    sensitive_meta_id: int | None = None,
) -> int:
    """Transition locked reimport jobs through the canonical model operations."""
    if status not in {
        UploadJob.Status.PROCESSING,
        UploadJob.Status.ANONYMIZED,
        UploadJob.Status.ERROR,
        UploadJob.Status.LOST,
    }:
        raise ValueError("Unsupported reimport upload job transition")
    selected = tuple(jobs)
    for job in selected:
        _guard_interrupted_upload_job(job)
        validate_upload_job_status_transition(
            current_status=job.status,
            target_status=status.value,
        )
    for job in selected:
        if status == UploadJob.Status.PROCESSING:
            mark_upload_job_processing(job)
        elif status == UploadJob.Status.ANONYMIZED:
            job.sensitive_meta_id = sensitive_meta_id
            job.mark_completed()
        elif status == UploadJob.Status.ERROR:
            job.mark_error(error_detail)
        else:
            job.mark_lost(error_detail)
    return len(selected)
