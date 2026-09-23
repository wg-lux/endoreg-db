from __future__ import annotations

from collections.abc import Sequence

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.services.lifecycle_state_machine import (
    OperationLifecycleEvent,
    OperationLifecycleState,
    reduce_operation_lifecycle,
)


_REPORT_IMPORT_STATES: dict[str, OperationLifecycleState] = {
    ReportImportAttempt.STATUS_IDLE: OperationLifecycleState.QUEUED,
    ReportImportAttempt.STATUS_ACTIVE: OperationLifecycleState.RUNNING,
    ReportImportAttempt.STATUS_SUCCEEDED: OperationLifecycleState.SUCCEEDED,
    ReportImportAttempt.STATUS_FAILED: OperationLifecycleState.FAILED,
    ReportImportAttempt.STATUS_LOST: OperationLifecycleState.LOST,
}

_REPORT_IMPORT_TARGET_STATES: dict[str, OperationLifecycleState] = {
    ReportImportAttempt.STATUS_ACTIVE: OperationLifecycleState.RUNNING,
    ReportImportAttempt.STATUS_SUCCEEDED: OperationLifecycleState.SUCCEEDED,
    ReportImportAttempt.STATUS_FAILED: OperationLifecycleState.FAILED,
    ReportImportAttempt.STATUS_LOST: OperationLifecycleState.LOST,
}


def _reduce_report_import_events(
    *,
    current_status: str,
    events: Sequence[OperationLifecycleEvent],
    target_status: str,
) -> None:
    try:
        current_state = _REPORT_IMPORT_STATES[current_status]
    except KeyError as exc:
        raise ValueError(f"unknown ReportImportAttempt status: {exc.args[0]}") from exc
    try:
        target_state = _REPORT_IMPORT_TARGET_STATES[target_status]
    except KeyError as exc:
        raise ValueError(
            f"unknown ReportImportAttempt target status: {exc.args[0]}"
        ) from exc
    reduced_state = reduce_operation_lifecycle(current_state, events)
    if reduced_state is not target_state:
        raise RuntimeError(
            "native report-import lifecycle reduction produced unexpected state: "
            f"{reduced_state.value}"
        )


def validate_report_import_claim(
    *,
    current_status: str,
    interrupted: bool,
) -> None:
    """Validate a new fenced claim, including automatic interrupted recovery."""
    if current_status == ReportImportAttempt.STATUS_IDLE:
        events = (
            OperationLifecycleEvent.CLAIM,
            OperationLifecycleEvent.START,
        )
    elif current_status == ReportImportAttempt.STATUS_ACTIVE and interrupted:
        events = (
            OperationLifecycleEvent.OWNERSHIP_LOST,
            OperationLifecycleEvent.RECONCILE_RETRY,
            OperationLifecycleEvent.RETRY_READY,
            OperationLifecycleEvent.CLAIM,
            OperationLifecycleEvent.START,
        )
    elif current_status in {
        ReportImportAttempt.STATUS_FAILED,
        ReportImportAttempt.STATUS_SUCCEEDED,
    }:
        events = (
            OperationLifecycleEvent.RETRY_REQUESTED,
            OperationLifecycleEvent.RETRY_READY,
            OperationLifecycleEvent.CLAIM,
            OperationLifecycleEvent.START,
        )
    elif current_status == ReportImportAttempt.STATUS_LOST:
        events = (
            OperationLifecycleEvent.RECONCILE_RETRY,
            OperationLifecycleEvent.RETRY_READY,
            OperationLifecycleEvent.CLAIM,
            OperationLifecycleEvent.START,
        )
    else:
        raise ValueError(
            "invalid ReportImportAttempt claim: "
            f"status={current_status} interrupted={interrupted}"
        )
    _reduce_report_import_events(
        current_status=current_status,
        events=events,
        target_status=ReportImportAttempt.STATUS_ACTIVE,
    )


def validate_report_import_success(*, current_status: str) -> None:
    _reduce_report_import_events(
        current_status=current_status,
        events=(OperationLifecycleEvent.SUCCEED,),
        target_status=ReportImportAttempt.STATUS_SUCCEEDED,
    )


def validate_report_import_failure(*, current_status: str) -> None:
    _reduce_report_import_events(
        current_status=current_status,
        events=(OperationLifecycleEvent.FAIL,),
        target_status=ReportImportAttempt.STATUS_FAILED,
    )


def validate_report_import_ownership_lost(*, current_status: str) -> None:
    _reduce_report_import_events(
        current_status=current_status,
        events=(OperationLifecycleEvent.OWNERSHIP_LOST,),
        target_status=ReportImportAttempt.STATUS_LOST,
    )
