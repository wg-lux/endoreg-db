"""Regression tests for removing SQLite-only policy from hub ingest.

These are control-flow unit tests. They do not exercise PostgreSQL locking,
file persistence, leases, or real broker delivery. Run them within the project's
configured pytest-django environment; they require no production shell access.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import cast
from unittest.mock import Mock, sentinel

import pytest
from django.db import IntegrityError, OperationalError

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub import ingest
from endoreg_db.tasks import run_video_upload_import_task

pytestmark = pytest.mark.no_db


class DriverError(Exception):
    """Supply driver diagnostics without performing a database operation."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("Synthetic driver failure")
        self.sqlstate = sqlstate


def _run_failed_video_import(
    monkeypatch: pytest.MonkeyPatch,
    error: IntegrityError,
) -> tuple[bool, object]:
    job = Mock(status=UploadJob.Status.PROCESSING.value)
    manager = Mock()
    manager.select_for_update.return_value.get.return_value = job
    execute = Mock(side_effect=error)
    monkeypatch.setattr(UploadJob, "objects", manager)
    monkeypatch.setattr(ingest.transaction, "atomic", Mock(return_value=nullcontext()))
    monkeypatch.setattr(
        ingest, "_acquire_video_upload_import_lease", Mock(return_value=sentinel.lease)
    )
    monkeypatch.setattr(ingest, "_execute_video_upload_import_attempt", execute)

    result = run_video_upload_import_task.run("test-job")

    manager.select_for_update.return_value.get.assert_called_once_with(id="test-job")
    execute.assert_called_once()
    return result, execute.call_args.args[0]


@pytest.mark.parametrize(
    ("sqlstate", "is_duplicate"),
    [
        ("23505", True),
        ("23503", False),
        ("23502", False),
        ("23514", False),
        (None, False),
    ],
)
def test_integrity_handler_preserves_postgresql_classification(
    monkeypatch: pytest.MonkeyPatch,
    sqlstate: str | None,
    is_duplicate: bool,
) -> None:
    error = IntegrityError("Synthetic ORM integrity failure")
    error.__cause__ = DriverError(sqlstate)
    duplicate = Mock()
    processing_retry = Mock()
    monkeypatch.setattr(ingest, "database_recovery_reason", Mock(return_value=None))
    monkeypatch.setattr(ingest, "_mark_duplicate_video_upload", duplicate)
    monkeypatch.setattr(
        ingest, "_schedule_video_upload_processing_retry", processing_retry
    )

    result, attempt = _run_failed_video_import(monkeypatch, error)
    assert result is False

    expected = duplicate if is_duplicate else processing_retry
    unexpected = processing_retry if is_duplicate else duplicate
    expected.assert_called_once_with(attempt, error)
    unexpected.assert_not_called()


@pytest.mark.parametrize("sqlite_code", [1555, 2067])
def test_legacy_sqlite_diagnostic_does_not_classify_as_postgresql_duplicate(
    monkeypatch: pytest.MonkeyPatch,
    sqlite_code: int,
) -> None:
    cause = DriverError(None)
    setattr(cause, "sqlite_errorcode", sqlite_code)
    error = IntegrityError("Synthetic legacy-driver integrity failure")
    error.__cause__ = cause
    duplicate = Mock()
    processing_retry = Mock()
    monkeypatch.setattr(ingest, "database_recovery_reason", Mock(return_value=None))
    monkeypatch.setattr(ingest, "_mark_duplicate_video_upload", duplicate)
    monkeypatch.setattr(
        ingest, "_schedule_video_upload_processing_retry", processing_retry
    )

    result, attempt = _run_failed_video_import(monkeypatch, error)
    assert result is False
    duplicate.assert_not_called()
    processing_retry.assert_called_once_with(attempt, error)


@pytest.mark.parametrize(
    "message",
    ["database is locked", "deadlock detected", "the connection is closed"],
)
def test_create_or_reuse_propagates_operational_errors_without_local_retry(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    error = OperationalError(message)
    create_attempt = Mock(side_effect=error)
    invalidate = Mock()
    monkeypatch.setattr(ingest, "_attempt_upload_job_create_or_reuse", create_attempt)
    monkeypatch.setattr(ingest, "_invalidate_upload_job_for_reingest", invalidate)

    with pytest.raises(OperationalError) as caught:
        ingest.create_or_reuse_upload_job(
            uploaded_file=None,
            content_type="video/mp4",
            content_hash="a" * 64,
        )

    assert caught.value is error
    create_attempt.assert_called_once()
    invalidate.assert_not_called()


def test_invalid_previous_job_is_still_reconciled_and_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = ingest.InvalidUploadJobReuse(
        job_id="previous-job",
        reason="Previous media is unusable",
        status=UploadJob.Status.LOST.value,
    )
    new_job = cast(UploadJob, sentinel.new_job)
    provenance = {"previous_upload_job_id": "previous-job"}
    create_attempt = Mock(side_effect=[invalid, (new_job, True)])
    invalidate = Mock(return_value=provenance)
    monkeypatch.setattr(ingest, "_attempt_upload_job_create_or_reuse", create_attempt)
    monkeypatch.setattr(ingest, "_invalidate_upload_job_for_reingest", invalidate)

    result = ingest.create_or_reuse_upload_job(
        uploaded_file=None,
        content_type="video/mp4",
        content_hash="a" * 64,
    )

    assert result == (new_job, True)
    assert create_attempt.call_count == 2
    invalidate.assert_called_once_with(invalid_reuse=invalid, created_by=None)
    assert create_attempt.call_args_list[0].kwargs["reingest_provenance_updates"] == {}
    assert (
        create_attempt.call_args_list[1].kwargs["reingest_provenance_updates"]
        == provenance
    )


def test_invalid_job_reconciliation_remains_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = ingest.InvalidUploadJobReuse(
        job_id="previous-job",
        reason="Previous media is unusable",
        status=UploadJob.Status.LOST.value,
    )
    create_attempt = Mock(return_value=invalid)
    invalidate = Mock(return_value={})
    monkeypatch.setattr(ingest, "UPLOAD_JOB_REUSE_ATTEMPTS", 3)
    monkeypatch.setattr(ingest, "_attempt_upload_job_create_or_reuse", create_attempt)
    monkeypatch.setattr(ingest, "_invalidate_upload_job_for_reingest", invalidate)

    with pytest.raises(RuntimeError, match="exhausted reconciliation attempts"):
        ingest.create_or_reuse_upload_job(
            uploaded_file=None,
            content_type="video/mp4",
            content_hash="a" * 64,
        )

    assert create_attempt.call_count == 3
    assert invalidate.call_count == 3
