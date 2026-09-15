# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest
from _pytest.logging import LogCaptureFixture

from endoreg_db.import_files import report_import_service as report_import_module
from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.utils.workload_timing import WorkloadOutcome


def _structured_timing_payloads(
    caplog: LogCaptureFixture,
) -> list[Mapping[str, object]]:
    payloads: list[Mapping[str, object]] = []
    for record in caplog.records:
        event_object = getattr(record, "structured_event", None)
        if isinstance(event_object, Mapping):
            event = cast(Mapping[str, object], event_object)
            if event.get("event") == "workload.timing":
                payloads.append(event)
    return payloads


@pytest.mark.parametrize(
    ("retry", "terminal_outcome", "expected_outcome", "expected_retry_bucket"),
    [
        (False, WorkloadOutcome.COMPLETED, "completed", "0"),
        (False, WorkloadOutcome.REUSED, "reused", "0"),
        (True, WorkloadOutcome.COMPLETED, "completed", "1"),
    ],
)
def test_report_import_emits_one_bounded_terminal_total_timing(
    caplog: LogCaptureFixture,
    tmp_path: Path,
    retry: bool,
    terminal_outcome: WorkloadOutcome,
    expected_outcome: str,
    expected_retry_bucket: str,
) -> None:
    service = object.__new__(ReportImportService)
    service.logger = logging.getLogger("tests.report_import")
    source_path = tmp_path / "patient-name.pdf"
    source_path.touch()
    result = Mock()
    setattr(service, "_validate_pdf_document", Mock())

    def locked_import(*_args: object) -> object:
        report_import_module._set_report_import_outcome(terminal_outcome)
        return result

    setattr(service, "_import_with_source_lock", locked_import)

    with (
        caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"),
        patch(
            "endoreg_db.utils.workload_timing.time.monotonic",
            side_effect=[30.0, 34.0],
        ),
    ):
        actual = service.import_and_anonymize(
            source_path,
            "secret-center",
            retry=retry,
        )

    assert actual is result
    payloads = _structured_timing_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0] == {
        "event": "workload.timing",
        "operation": "report_import",
        "phase": "total",
        "outcome": expected_outcome,
        "task_family": "report_llm_import",
        "queue": "pipeline",
        "retry_bucket": expected_retry_bucket,
        "duration_seconds": 4.0,
        "metric_name": "workload_duration_seconds",
        "metric_value": 4.0,
    }
    assert str(source_path) not in caplog.text
    assert "secret-center" not in caplog.text


def test_report_import_failure_emits_once_without_exception_payload(
    caplog: LogCaptureFixture,
    tmp_path: Path,
) -> None:
    service = object.__new__(ReportImportService)
    service.logger = logging.getLogger("tests.report_import")
    source_path = tmp_path / "patient-name.pdf"
    source_path.touch()
    setattr(service, "_validate_pdf_document", Mock())
    setattr(
        service,
        "_import_with_source_lock",
        Mock(side_effect=RuntimeError("protected patient detail")),
    )

    with (
        caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"),
        patch(
            "endoreg_db.utils.workload_timing.time.monotonic",
            side_effect=[40.0, 41.0],
        ),
        pytest.raises(RuntimeError, match="protected patient detail"),
    ):
        service.import_and_anonymize(source_path, "secret-center")

    payloads = _structured_timing_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["outcome"] == "failed"
    assert payloads[0]["phase"] == "total"
    assert str(source_path) not in caplog.text
    assert "protected patient detail" not in caplog.text
