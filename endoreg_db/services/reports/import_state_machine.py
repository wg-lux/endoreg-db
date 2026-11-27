from __future__ import annotations

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.services.runtime.status_writers import (
    StatusMachine,
    validate_status_transition,
)


def _validate_report_transition(*, current_status: str, target_status: str) -> None:
    if current_status not in dict(ReportImportAttempt.STATUS_CHOICES):
        raise ValueError(f"unknown ReportImportAttempt status: {current_status}")
    validate_status_transition(
        StatusMachine.REPORT_IMPORT_ATTEMPT,
        current_status,
        target_status,
        error_message=f"invalid operation lifecycle transition: {current_status} -> {target_status}",
    )


def validate_report_import_claim(*, current_status: str, interrupted: bool) -> None:
    """Native claim admission; callers establish and fence expired ownership."""
    validate_status_transition(
        StatusMachine.REPORT_IMPORT_LEASE_RECOVERY
        if interrupted
        else StatusMachine.REPORT_IMPORT_ATTEMPT,
        current_status,
        ReportImportAttempt.STATUS_ACTIVE,
        error_message=f"invalid ReportImportAttempt claim: status={current_status} interrupted={interrupted}",
    )


def validate_report_import_success(*, current_status: str) -> None:
    _validate_report_transition(
        current_status=current_status,
        target_status=ReportImportAttempt.STATUS_SUCCEEDED,
    )


def validate_report_import_failure(*, current_status: str) -> None:
    _validate_report_transition(
        current_status=current_status,
        target_status=ReportImportAttempt.STATUS_FAILED,
    )


def validate_report_import_ownership_lost(*, current_status: str) -> None:
    _validate_report_transition(
        current_status=current_status,
        target_status=ReportImportAttempt.STATUS_LOST,
    )
