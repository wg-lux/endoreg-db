from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.services.hub.transfer_logging import (
    json_block,
    kv,
    path_info,
    sanitize,
    section,
    transfer_summary,
)


def test_transfer_console_logging_is_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ENDOREG_TRANSFER_VERBOSE", raising=False)

    section("RECEIVER: CREATE TRANSFER")
    kv("Transfer status", "applied")
    json_block("Payload", {"resource_kind": "video"})

    assert capsys.readouterr().out == ""


def test_transfer_json_logging_redacts_sensitive_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ENDOREG_TRANSFER_VERBOSE", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    sensitive_values = {
        "raw_text": "Max Mustermann raw report",
        "anonymized_text": "Potentially identifying free text",
        "raw_meta": "private metadata",
        "provenance": "private provenance",
        "shared_secret": "node-secret",
        "editor_payload": "private editor state",
    }

    json_block(
        "Sanitized transfer payload",
        {
            **sensitive_values,
            "transfer_key": "site-a__report__private",
            "resource_kind": "report",
        },
    )

    output = capsys.readouterr().out
    assert "resource_kind" in output
    assert "report" in output
    assert "<redacted:transfer_sensitive>" in output
    assert "site-a__report__private" not in output
    for sensitive_value in sensitive_values.values():
        assert sensitive_value not in output


def test_transfer_summary_hashes_portable_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ENDOREG_TRANSFER_VERBOSE", "true")
    monkeypatch.setenv("NO_COLOR", "1")

    transfer_summary(
        transfer_key="site-a__video__private",
        resource_kind="video",
        source_node_key="private-source-node",
        target_node_key="private-target-node",
        resource_hash="private-resource-hash",
        transfer_mode="metadata_and_processed_media",
    )

    output = capsys.readouterr().out
    assert "video" in output
    assert "metadata_and_processed_media" in output
    assert "<sha256:" in output
    assert "site-a__video__private" not in output
    assert "private-source-node" not in output
    assert "private-target-node" not in output
    assert "private-resource-hash" not in output


def test_transfer_path_logging_uses_hash_reference(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ENDOREG_TRANSFER_VERBOSE", "yes")
    monkeypatch.setenv("NO_COLOR", "1")
    sensitive_path = tmp_path / "patient-max-mustermann.pdf"
    sensitive_path.write_bytes(b"processed")

    path_info(label="Processed artifact", path=sensitive_path)

    output = capsys.readouterr().out
    assert "path_sha256" in output
    assert ".pdf" in output
    assert "patient-max-mustermann" not in output
    assert str(sensitive_path) not in output
    assert "Processed artifact exists" in output


def test_transfer_sanitizer_never_uses_unknown_object_representation() -> None:
    class SensitiveObject:
        def __str__(self) -> str:
            return "private-object-value"

    sanitized = sanitize(SensitiveObject())

    assert sanitized == "<SensitiveObject>"
