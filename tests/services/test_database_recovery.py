from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import IntegrityError, OperationalError, ProgrammingError

from endoreg_db.services.jobs.error_handling import (
    database_recovery_reason,
    retry_database_operation,
)


class DriverError(Exception):
    def __init__(self, sqlstate: str, column: str = "future_required_column") -> None:
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(
            table_name="endoreg_db_videohlsartifact", column_name=column
        )


@pytest.mark.parametrize(
    ("state", "column", "expected"),
    [
        ("23502", "future_required_column", "database_schema_mismatch"),
        ("23502", "encoding_profile_name", None),
        ("23505", "future_required_column", None),
        ("23503", "future_required_column", None),
        ("42703", "future_required_column", "database_schema_mismatch"),
        ("42P01", "future_required_column", "database_schema_mismatch"),
    ],
)
def test_database_recovery_uses_driver_metadata(
    state: str,
    column: str,
    expected: str | None,
) -> None:
    error = IntegrityError("must never parse protected failing row")
    error.__cause__ = DriverError(state, column)
    assert database_recovery_reason(error) == expected


@pytest.mark.parametrize(
    ("attempt", "delay"), [(0, 60), (1, 120), (4, 900), (10000, 900)]
)
def test_database_retry_is_capped_unlimited_and_redacted(
    attempt: int, delay: int
) -> None:
    retry = Mock(return_value=RuntimeError("scheduled"))
    with pytest.raises(RuntimeError, match="scheduled"):
        retry_database_operation(
            retry=retry,
            error=OperationalError("protected failing row"),
            retries=attempt,
            job_name="import",
            subject_id=1,
        )
    assert retry.call_args.kwargs["max_retries"] is None
    assert retry.call_args.kwargs["throw"] is False
    assert retry.call_args.kwargs["countdown"] == delay
    assert str(retry.call_args.kwargs["exc"]) == "database_unavailable"


def test_unknown_database_error_is_not_blindly_retried() -> None:
    retry = Mock()
    error = ProgrammingError("invalid query")
    with pytest.raises(ProgrammingError):
        retry_database_operation(
            retry=retry,
            error=error,
            retries=0,
            job_name="import",
            subject_id=1,
        )
    retry.assert_not_called()
