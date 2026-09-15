# pyright: reportPrivateUsage=false
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest
from _pytest.logging import LogCaptureFixture

from endoreg_db.import_files import video_import_service as video_import_module
from endoreg_db.import_files.video_import_service import VideoImportService
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
def test_video_import_emits_one_bounded_terminal_total_timing(
    caplog: LogCaptureFixture,
    tmp_path: Path,
    retry: bool,
    terminal_outcome: WorkloadOutcome,
    expected_outcome: str,
    expected_retry_bucket: str,
) -> None:
    service = object.__new__(VideoImportService)
    result = Mock()

    def import_core(**_kwargs: object) -> object:
        video_import_module._set_video_import_outcome(terminal_outcome)
        return result

    setattr(service, "_import_and_anonymize", import_core)
    sensitive_path = tmp_path / "protected" / "patient-name.mp4"

    with (
        caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"),
        patch(
            "endoreg_db.utils.workload_timing.time.monotonic",
            side_effect=[10.0, 12.5],
        ),
    ):
        actual = service.import_and_anonymize(
            sensitive_path,
            "secret-center",
            "secret-processor",
            retry=retry,
        )

    assert actual is result
    payloads = _structured_timing_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0] == {
        "event": "workload.timing",
        "operation": "video_import",
        "phase": "total",
        "outcome": expected_outcome,
        "task_family": "video_upload_import",
        "queue": "pipeline",
        "retry_bucket": expected_retry_bucket,
        "duration_seconds": 2.5,
        "metric_name": "workload_duration_seconds",
        "metric_value": 2.5,
    }
    assert str(sensitive_path) not in caplog.text
    assert "secret-center" not in caplog.text
    assert "secret-processor" not in caplog.text


def test_video_import_failure_emits_once_without_exception_payload(
    caplog: LogCaptureFixture,
    tmp_path: Path,
) -> None:
    service = object.__new__(VideoImportService)
    sensitive_path = tmp_path / "protected" / "patient-name.mp4"
    error = RuntimeError("protected patient detail")
    setattr(service, "_import_and_anonymize", Mock(side_effect=error))

    with (
        caplog.at_level(logging.INFO, logger="endoreg_db.workload_timing"),
        patch(
            "endoreg_db.utils.workload_timing.time.monotonic",
            side_effect=[20.0, 21.0],
        ),
        pytest.raises(RuntimeError, match="protected patient detail"),
    ):
        service.import_and_anonymize(
            sensitive_path,
            "secret-center",
            "secret-processor",
        )

    payloads = _structured_timing_payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["outcome"] == "failed"
    assert payloads[0]["phase"] == "total"
    assert str(sensitive_path) not in caplog.text
    assert "protected patient detail" not in caplog.text
