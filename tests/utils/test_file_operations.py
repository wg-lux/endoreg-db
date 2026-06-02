from __future__ import annotations

import errno
import hashlib
import logging
from pathlib import Path

import pytest

from endoreg_db.utils.filesystem import file_operations
from endoreg_db.utils.filesystem.file_operations import (
    atomic_handoff_file,
    atomic_move_file,
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
    sha256_file,
)


class _StreamingStorage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.range_calls: list[tuple[str, int, int, int]] = []

    def get_plaintext_size(self, name: str) -> int:
        return len(self.payload)

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ):
        self.range_calls.append((name, start, end, chunk_size))
        yield self.payload[start : end + 1]

    def open(self, *args, **kwargs):
        raise AssertionError("sha256_file should stream FieldFile bytes")


class _StreamingFieldFile:
    def __init__(self, payload: bytes, name: str = "processed/video.mp4"):
        self.name = name
        self.storage = _StreamingStorage(payload)


def _file_operation_events(caplog) -> list[dict[str, object]]:
    return [
        record.structured_event
        for record in caplog.records
        if record.name == "endoreg_db.utils.filesystem.file_operations"
        and getattr(record, "structured_event", {}).get("event") == "file_operation"
    ]


@pytest.mark.unit
def test_sha256_file_hashes_field_file_through_streaming_storage():
    payload = b"streamed plaintext payload"
    field_file = _StreamingFieldFile(payload)

    digest = sha256_file(field_file)

    assert digest == hashlib.sha256(payload).hexdigest()
    assert field_file.storage.range_calls == [
        ("processed/video.mp4", 0, len(payload) - 1, 1024 * 1024)
    ]


@pytest.mark.unit
def test_atomic_write_file_replaces_destination_and_emits_json_log(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    destination = tmp_path / "nested" / "payload.bin"

    result = atomic_write_file(
        destination=destination,
        content=(chunk for chunk in (b"abc", b"def")),
        required_bytes=6,
        file_mode=0o600,
    )

    assert result == destination
    assert destination.read_bytes() == b"abcdef"
    assert oct(destination.stat().st_mode & 0o777) == "0o600"
    assert list(destination.parent.glob("payload.bin.tmp.*")) == []
    assert {
        "event": "file_operation",
        "operation": "write",
        "status": "ok",
        "destination_path": file_operations.path_reference(destination),
        "bytes": 6,
    } in _file_operation_events(caplog)
    assert str(destination) not in caplog.text


@pytest.mark.unit
def test_atomic_write_file_removes_partial_temp_file_on_generator_failure(
    caplog, tmp_path
):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    destination = tmp_path / "payload.bin"

    def failing_content():
        yield b"partial"
        raise RuntimeError("write source failed")

    with pytest.raises(RuntimeError, match="write source failed"):
        atomic_write_file(destination=destination, content=failing_content())

    assert not destination.exists()
    assert list(tmp_path.glob("payload.bin.tmp.*")) == []
    events = _file_operation_events(caplog)
    assert events[-1]["operation"] == "write"
    assert events[-1]["status"] == "error"
    assert events[-1]["destination_path"] == file_operations.path_reference(destination)
    assert events[-1]["bytes"] == 7
    assert "write source failed" in str(events[-1]["detail"])


@pytest.mark.unit
def test_atomic_handoff_file_fsyncs_and_promotes_final_name(
    caplog,
    monkeypatch,
    tmp_path,
):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    destination = tmp_path / "incoming.mp4"
    fsync_calls: list[int] = []
    original_fsync = file_operations.os.fsync

    def recording_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        original_fsync(fd)

    monkeypatch.setattr(file_operations.os, "fsync", recording_fsync)

    result = atomic_handoff_file(
        destination=destination,
        content=(chunk for chunk in (b"video", b"-payload")),
        required_bytes=13,
        file_mode=0o600,
    )

    assert result == destination
    assert destination.read_bytes() == b"video-payload"
    assert oct(destination.stat().st_mode & 0o777) == "0o600"
    assert list(tmp_path.glob("incoming.mp4.part.*")) == []
    assert len(fsync_calls) >= 1
    assert {
        "event": "file_operation",
        "operation": "handoff",
        "status": "ok",
        "destination_path": file_operations.path_reference(destination),
        "bytes": 13,
    } in _file_operation_events(caplog)


@pytest.mark.unit
def test_atomic_handoff_file_removes_temp_file_on_byte_count_mismatch(
    caplog,
    tmp_path,
):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    destination = tmp_path / "incoming.mp4"

    with pytest.raises(ValueError, match="byte count mismatch"):
        atomic_handoff_file(
            destination=destination,
            content=(b"too-short",),
            required_bytes=128,
        )

    assert not destination.exists()
    assert list(tmp_path.glob("incoming.mp4.part.*")) == []
    events = _file_operation_events(caplog)
    assert events[-1]["operation"] == "handoff"
    assert events[-1]["status"] == "error"
    assert events[-1]["destination_path"] == file_operations.path_reference(destination)
    assert events[-1]["bytes"] == 9


@pytest.mark.unit
def test_atomic_move_file_falls_back_to_copy_then_unlink_on_cross_device_error(
    caplog, monkeypatch, tmp_path
):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    source = tmp_path / "source.bin"
    destination = tmp_path / "other" / "destination.bin"
    source.write_bytes(b"move-me")
    original_replace = file_operations.os.replace
    calls = 0

    def replace_with_first_call_cross_device(src: str | Path, dst: str | Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        original_replace(src, dst)

    monkeypatch.setattr(
        file_operations.os, "replace", replace_with_first_call_cross_device
    )

    result = atomic_move_file(source=source, destination=destination)

    assert result == destination
    assert destination.read_bytes() == b"move-me"
    assert not source.exists()
    assert calls == 2
    events = _file_operation_events(caplog)
    assert any(
        event["operation"] == "copy" and event["status"] == "ok" for event in events
    )
    assert any(
        event["operation"] == "unlink" and event["status"] == "ok" for event in events
    )
    assert any(
        event["operation"] == "move" and event["status"] == "ok" for event in events
    )


@pytest.mark.unit
def test_safe_unlink_file_missing_required_path_logs_and_raises(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    missing = tmp_path / "missing.bin"

    with pytest.raises(FileNotFoundError):
        safe_unlink_file(missing, missing_ok=False)

    assert _file_operation_events(caplog)[-1] == {
        "event": "file_operation",
        "operation": "unlink",
        "status": "error",
        "source_path": file_operations.path_reference(missing),
        "detail": "missing file",
    }


@pytest.mark.unit
def test_ensure_directory_and_safe_rmtree_emit_structured_events(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    target = tmp_path / "created" / "nested"

    ensure_directory(target, dir_mode=0o700)
    (target / "child.txt").write_text("payload")
    safe_rmtree(target)

    assert not target.exists()
    events = _file_operation_events(caplog)
    assert any(
        event["operation"] == "mkdir"
        and event["status"] == "ok"
        and event["destination_path"] == file_operations.path_reference(target)
        for event in events
    )
    assert any(
        event["operation"] == "rmtree"
        and event["status"] == "ok"
        and event["source_path"] == file_operations.path_reference(target)
        for event in events
    )


@pytest.mark.unit
def test_safe_rmtree_retries_directory_not_empty_race(monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.filesystem.file_operations")
    target = tmp_path / "racy"
    ensure_directory(target)
    atomic_write_file(destination=target / "child.txt", content=(b"payload",))
    original_rmtree = file_operations.shutil.rmtree
    calls = 0

    def racy_rmtree(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.ENOTEMPTY, "Directory not empty", str(path))
        return original_rmtree(path)

    monkeypatch.setattr(file_operations.shutil, "rmtree", racy_rmtree)

    safe_rmtree(target)

    assert calls == 2
    assert not target.exists()
    events = _file_operation_events(caplog)
    assert any(
        event["operation"] == "rmtree" and event["status"] == "retry"
        for event in events
    )
    assert any(
        event["operation"] == "rmtree" and event["status"] == "ok" for event in events
    )
