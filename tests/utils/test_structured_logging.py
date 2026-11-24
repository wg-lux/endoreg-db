from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest
from _pytest.logging import LogCaptureFixture
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

from endoreg_db.utils.structured_logging import (
    StructuredJsonFormatter,
    emit_structured_event,
    path_reference,
    request_log_context,
    safe_log_payload,
    safe_log_value,
)

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (7, 7),
        (2.5, 2.5),
        ("ready", "ready"),
        (b"abc", {"bytes_redacted": True, "length": 3}),
        (
            ValueError("invalid input"),
            {"type": "ValueError", "message": "invalid input"},
        ),
        (1j, "1j"),
    ],
)
def test_safe_log_value_scalar_contract(value: object, expected: JsonValue) -> None:
    actual = safe_log_value(value)

    assert actual == expected
    assert type(actual) is type(expected)


@pytest.mark.parametrize("container", [list, tuple, set, frozenset])
@pytest.mark.parametrize("size", [0, 1, 100, 101])
def test_safe_log_value_collection_limit(
    container: Callable[
        [range], list[int] | tuple[int, ...] | set[int] | frozenset[int]
    ],
    size: int,
) -> None:
    value = container(range(size))
    expected = list(value)[:100]

    actual = safe_log_value(value)

    assert actual == expected
    assert len(value) == size


@pytest.mark.parametrize(
    "value", [Path("report.pdf"), b"abc", {"x": 1}, [1], ValueError("bad")]
)
def test_sensitive_key_precedes_value_conversion(value: object) -> None:
    actual = safe_log_value(value, key="API_TOKEN")

    assert actual == "<redacted:sensitive>"


@pytest.mark.parametrize("container", [list, tuple, set, frozenset])
def test_collection_elements_are_sanitized(
    container: Callable[[list[bytes]], object],
) -> None:
    value = container([b"abc"])

    actual = safe_log_value(value)

    assert actual == [{"bytes_redacted": True, "length": 3}]


def test_invalid_path_encoding_raises_instead_of_returning_unredacted_text() -> None:
    value = "/\ud800"

    with pytest.raises(UnicodeEncodeError):
        safe_log_value(value)


def test_safe_log_value_nested_mapping_uses_each_child_key() -> None:
    value = {1: ({"password": b"abc", "count": 2},), "secret": ["hidden"]}
    expected = {
        "1": [{"password": "<redacted:sensitive>", "count": 2}],
        "secret": "<redacted:sensitive>",
    }

    actual = safe_log_value(value)

    assert actual == expected
    assert value["secret"] == ["hidden"]


def test_safe_log_payload_and_nested_mapping_have_identical_outputs() -> None:
    value: JsonObject = {"items": [{"cookie": "hidden", "ok": True}], "empty": {}}

    actual = safe_log_payload(value)

    assert actual == {
        "items": [{"cookie": "<redacted:sensitive>", "ok": True}],
        "empty": {},
    }
    assert actual == safe_log_value(value)


def test_safe_log_value_path_and_exception_redaction() -> None:
    path = Path("report.pdf")

    path_value = safe_log_value(path)
    error_value = safe_log_value(ValueError("report.pdf"))

    assert path_value == {
        "path_sha256": "6466e450a16b77b865c5829d6b6c56d9f892956475642dbeb9ccc4340fe01b15",
        "suffix": ".pdf",
    }
    assert error_value == {
        "type": "ValueError",
        "message": "<redacted:media_filename:sha256=6466e450a16b77b865c5829d6b6c56d9f892956475642dbeb9ccc4340fe01b15>",
    }


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


def test_nested_request_context_restores_outer_identity(
    caplog: LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.structured_logging")
    with caplog.at_level(logging.INFO, logger="tests.structured_logging"):
        with request_log_context() as outer:
            with request_log_context() as inner:
                emit_structured_event(logger, "inner")
            emit_structured_event(logger, "outer")
        emit_structured_event(logger, "outside")

    events = [
        cast(Mapping[str, object], getattr(record, "structured_event"))
        for record in caplog.records
    ]
    assert outer != inner
    assert events[0]["request_id"] == inner
    assert events[1]["request_id"] == outer
    assert "request_id" not in events[2]
