from __future__ import annotations

import pytest

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.services.reports.import_state_machine import (
    validate_report_import_claim,
    validate_report_import_failure,
    validate_report_import_success,
    validate_report_import_ownership_lost,
)

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    ("current_status", "interrupted"),
    [
        (ReportImportAttempt.STATUS_IDLE, False),
        (ReportImportAttempt.STATUS_ACTIVE, True),
        (ReportImportAttempt.STATUS_FAILED, False),
        (ReportImportAttempt.STATUS_SUCCEEDED, False),
        (ReportImportAttempt.STATUS_LOST, False),
    ],
)
def test_report_import_claim_uses_native_retry_paths(
    current_status: str,
    interrupted: bool,
) -> None:
    validate_report_import_claim(
        current_status=current_status,
        interrupted=interrupted,
    )


def test_live_report_import_cannot_be_reclaimed() -> None:
    with pytest.raises(ValueError, match="invalid ReportImportAttempt claim"):
        validate_report_import_claim(
            current_status=ReportImportAttempt.STATUS_ACTIVE,
            interrupted=False,
        )


def test_report_import_terminal_events_use_native_reducer() -> None:
    validate_report_import_success(
        current_status=ReportImportAttempt.STATUS_ACTIVE,
    )
    validate_report_import_failure(
        current_status=ReportImportAttempt.STATUS_ACTIVE,
    )


def test_report_import_cannot_skip_active_state() -> None:
    with pytest.raises(ValueError, match="invalid operation lifecycle transition"):
        validate_report_import_success(
            current_status=ReportImportAttempt.STATUS_IDLE,
        )


def test_report_import_ownership_loss_has_durable_lost_state() -> None:
    # Arrange
    status_values = {value for value, _label in ReportImportAttempt.STATUS_CHOICES}

    # Act
    lost_status = getattr(ReportImportAttempt, "STATUS_LOST", None)

    # Assert
    assert lost_status == "lost"
    assert lost_status in status_values


@pytest.mark.parametrize(
    "current_status", ["idle", "active", "succeeded", "failed", "lost", "", "unknown"]
)
@pytest.mark.parametrize("interrupted", [False, True])
def test_report_claim_contract_is_exhaustive(
    current_status: str, interrupted: bool
) -> None:
    permitted = current_status in {"idle", "succeeded", "failed", "lost"} or (
        current_status == "active" and interrupted
    )
    if permitted:
        validate_report_import_claim(
            current_status=current_status, interrupted=interrupted
        )
    else:
        with pytest.raises(ValueError):
            validate_report_import_claim(
                current_status=current_status, interrupted=interrupted
            )


@pytest.mark.parametrize(
    "current_status", ["idle", "active", "succeeded", "failed", "lost", "", "unknown"]
)
@pytest.mark.parametrize("outcome", ["succeeded", "failed", "lost"])
def test_report_outcome_contract_is_exhaustive(
    current_status: str, outcome: str
) -> None:
    validators = {
        "succeeded": validate_report_import_success,
        "failed": validate_report_import_failure,
        "lost": validate_report_import_ownership_lost,
    }
    allowed = {
        "idle": {"failed", "lost"},
        "active": {"succeeded", "failed", "lost"},
        "succeeded": {"succeeded"},
        "failed": {"failed"},
        "lost": {"lost"},
    }
    if outcome in allowed.get(current_status, set()):
        validators[outcome](current_status=current_status)
    else:
        with pytest.raises(ValueError):
            validators[outcome](current_status=current_status)
