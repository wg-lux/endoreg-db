from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from _pytest.logging import LogCaptureFixture

from endoreg_db.utils.structured_logging import (
    StructuredJsonFormatter,
    emit_structured_event,
    path_reference,
)


def test_structured_json_formatter_outputs_json_and_redacts_sensitive_values() -> None:
    record = logging.LogRecord(
        name="tests.structured_logging",
        level=logging.WARNING,
        pathname=__file__,
        lineno=12,
        msg="failed to process /patients/Jane Doe/raw_video.mp4",
        args=(),
        exc_info=None,
    )
    setattr(
        record,
        "structured_event",
        {
            "event": "security.auth_failed",
            "shared_secret": "do-not-log",
            "master_key": "do-not-log",
            "source_path": Path("/patients/Jane Doe/raw_video.mp4"),
            "raw_media": b"raw-bytes",
        },
    )

    payload = json.loads(StructuredJsonFormatter().format(record))

    assert payload["event"] == "security.auth_failed"
    assert payload["level"] == "WARNING"
    assert payload["message"] == "security.auth_failed"
    assert payload["shared_secret"] == "<redacted:sensitive>"
    assert payload["master_key"] == "<redacted:sensitive>"
    assert payload["source_path"] == path_reference(
        Path("/patients/Jane Doe/raw_video.mp4")
    )
    assert payload["raw_media"] == "<redacted:sensitive>"
    assert "do-not-log" not in json.dumps(payload)
    assert "Jane Doe" not in json.dumps(payload)
    assert "raw-bytes" not in json.dumps(payload)


def test_emit_structured_event_adds_sanitized_record_extra(
    caplog: LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.structured_logging")

    with caplog.at_level(logging.INFO, logger="tests.structured_logging"):
        emit_structured_event(
            logger,
            "file_operation",
            operation="write",
            destination_path=path_reference(Path("/patients/Jane Doe/report.pdf")),
        )

    event = cast(
        Mapping[str, object],
        getattr(caplog.records[-1], "structured_event"),
    )
    assert event["event"] == "file_operation"
    assert event["destination_path"] == path_reference(
        Path("/patients/Jane Doe/report.pdf")
    )
    assert "Jane Doe" not in caplog.text
