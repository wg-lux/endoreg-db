from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.logger_conf import get_logging_config
from endoreg_db.utils.structured_logging import path_reference

pytestmark = pytest.mark.no_db


def _file_events(caplog: pytest.LogCaptureFixture) -> list[JsonObject]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "endoreg_db.utils.file_operations"
    ]


def test_logging_config_creates_directory_with_audited_wrapper(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log_dir = tmp_path / "nested" / "logs"

    with caplog.at_level(logging.INFO, logger="endoreg_db.utils.file_operations"):
        config = get_logging_config(["example.worker"], log_dir=log_dir)

    assert log_dir.is_dir()
    assert config["handlers"]["file_example.worker"].get("filename") == (
        log_dir / "example.worker.log"
    )
    assert config["handlers"]["file_root"].get("filename") == log_dir / "root.log"
    assert list(log_dir.iterdir()) == []
    assert _file_events(caplog) == [
        {
            "event": "file_operation",
            "operation": "mkdir",
            "status": "ok",
            "destination_path": path_reference(log_dir),
        }
    ]
    assert str(log_dir) not in caplog.text


def test_logging_config_propagates_audited_directory_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log_path = tmp_path / "existing-file"
    log_path.write_text("preserved")

    with caplog.at_level(logging.INFO, logger="endoreg_db.utils.file_operations"):
        with pytest.raises(FileExistsError):
            get_logging_config(["example"], log_dir=log_path)

    assert log_path.read_text() == "preserved"
    event = _file_events(caplog)[-1]
    assert event["operation"] == "mkdir"
    assert event["status"] == "error"
    assert event["destination_path"] == path_reference(log_path)
    assert str(log_path) not in caplog.text
