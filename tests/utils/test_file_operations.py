from __future__ import annotations
import base64
import hashlib
import io
import os
import errno
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import pytest
from _pytest.logging import LogCaptureFixture
from django.db.models.fields.files import FieldFile
from pytest import MonkeyPatch

from endoreg_db.utils import file_operations
from endoreg_db.utils.file_operations import (
    atomic_create_file,
    atomic_handoff_file,
    atomic_move_file,
    atomic_write_file,
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
    get_file_hash,
)
from endoreg_db.config.env import BASE_DIR
from django.core.files.base import ContentFile

from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from django.db.models import FileField
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.utils.encryption.encryption import encrypt_stream


class _StreamingStorage:
    def __init__(self, payload: bytes) -> None:
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
    ) -> Iterable[bytes]:
        self.range_calls.append((name, start, end, chunk_size))
        yield self.payload[start : end + 1]

    def open(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("get_file_hash should stream FieldFile bytes")


class _StreamingFieldFile:
    def __init__(self, payload: bytes, name: str = "tests/assets/test.mp4") -> None:
        self.name = name
        self.path: Path = Path(BASE_DIR / "tests/assets/test.mp4")
        self.storage = _StreamingStorage(payload)


def _file_operation_events(caplog: LogCaptureFixture) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for record in caplog.records:
        if record.name != "endoreg_db.utils.file_operations":
            continue
        structured_event = getattr(record, "structured_event", None)
        if not isinstance(structured_event, dict):
            continue
        event = cast(dict[str, object], structured_event)
        if event.get("event") == "file_operation":
            events.append(event)
    return events


@pytest.fixture
def master_key_env(monkeypatch: MonkeyPatch) -> bytes:
    """Provide a valid urlsafe-base64 32-byte master key in the environment."""
    raw_key = os.urandom(32)
    b64_key = base64.urlsafe_b64encode(raw_key).decode("ascii")
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY", b64_key)
    return raw_key


class _MockFieldFile:
    """Lightweight mock simulating Django FieldFile interface for file paths."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = str(path)


@pytest.mark.unit
@pytest.mark.parametrize("payload", [b"", b"authenticated plaintext" * 100])
def test_get_file_hash_streams_plaintext_without_local_path(payload: bytes) -> None:
    source = _StreamingFieldFile(payload)

    assert get_file_hash(cast(FieldFile, source)) == hashlib.sha256(payload).hexdigest()
    assert len(source.storage.range_calls) == (1 if payload else 0)


@pytest.mark.unit
def test_get_file_hash_reads_encrypted_storage_plaintext(
    tmp_path: Path, master_key_env: bytes
) -> None:
    storage = EncryptedStorage(location=tmp_path, master_key=master_key_env)
    payload = b"stored encrypted artifact"
    name = storage.save("source.mp4", ContentFile(payload))
    source = FieldFile(VideoFile(), FileField(storage=storage), name)

    assert get_file_hash(source) == hashlib.sha256(payload).hexdigest()


@pytest.mark.unit
def test_get_file_hash_plaintext_file(tmp_path: Path) -> None:
    """Verify hashing a standard plaintext OS file yields correct SHA-256."""
    payload = b"unencrypted video content stream"
    file_path = tmp_path / "test.mp4"
    file_path.write_bytes(payload)

    expected_hash = hashlib.sha256(payload).hexdigest()
    assert get_file_hash(file_path) == expected_hash


@pytest.mark.unit
def test_get_file_hash_encrypted_decryption_parity(
    tmp_path: Path, master_key_env: bytes
) -> None:
    """
    Verify hashing an encrypted (LXENC01) file yields the exact SHA-256 hash
    of its underlying PLAINTEXT, maintaining parity with the raw file.
    """
    payload = b"encrypted video content stream payload"
    expected_hash = hashlib.sha256(payload).hexdigest()

    # 1. Write an encrypted LXENC01 file
    enc_file_path = tmp_path / "encrypted.mp4"
    with open(enc_file_path, "wb") as dst:
        encrypt_stream(
            source=io.BytesIO(payload),
            destination=dst,
            master_key=master_key_env,
        )

    # 2. Assert Rust unwraps encryption and returns plaintext digest
    digest = get_file_hash(enc_file_path)
    assert digest == expected_hash


@pytest.mark.unit
def test_get_file_hash_accepts_field_file_instance(
    tmp_path: Path, master_key_env: bytes
) -> None:
    """Verify FieldFile objects are resolved properly by get_file_hash."""
    payload = b"fieldfile resolution test payload"
    expected_hash = hashlib.sha256(payload).hexdigest()

    file_path = tmp_path / "fieldfile.mp4"
    file_path.write_bytes(payload)
    field_file = _MockFieldFile(file_path)

    digest = get_file_hash(cast(FieldFile, field_file))
    assert digest == expected_hash


@pytest.mark.unit
def test_get_file_hash_idempotency_and_types(tmp_path: Path) -> None:
    """Verify Path, string paths, and FieldFile objects return identical results."""
    payload = b"idempotent payload test"
    file_path = tmp_path / "test.bin"
    file_path.write_bytes(payload)

    hash_from_path = get_file_hash(file_path)
    hash_from_str = get_file_hash(str(file_path))
    hash_from_field = get_file_hash(cast(FieldFile, _MockFieldFile(file_path)))

    assert hash_from_path == hash_from_str == hash_from_field


@pytest.mark.unit
def test_get_file_hash_raises_on_none_or_missing() -> None:
    """Verify invalid inputs or None raise ValueError as expected."""
    with pytest.raises(ValueError, match="HASH COULD NOT BE CREATED"):
        get_file_hash(None)


@pytest.mark.unit
def test_atomic_write_file_replaces_destination_and_emits_json_log(
    caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
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
    caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
    destination = tmp_path / "payload.bin"

    def failing_content() -> Iterable[bytes]:
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
def test_atomic_create_file_never_replaces_existing_content(tmp_path: Path) -> None:
    destination = tmp_path / "lock.json"

    atomic_create_file(destination=destination, content=(b"first",), file_mode=0o600)

    with pytest.raises(FileExistsError):
        atomic_create_file(destination=destination, content=(b"second",))
    assert destination.read_bytes() == b"first"
    assert not tuple(tmp_path.glob("lock.json.tmp.*"))


@pytest.mark.unit
def test_atomic_handoff_file_fsyncs_and_promotes_final_name(
    caplog: LogCaptureFixture,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
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
    caplog: LogCaptureFixture,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
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
    caplog: LogCaptureFixture, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
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
def test_atomic_move_file_copy_failure_keeps_source_and_removes_partial_destination(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "ingest" / "source.bin"
    destination = tmp_path / "protected" / "destination.bin"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"complete source payload")
    calls = 0

    def replace_with_cross_device_error(src: str | Path, dst: str | Path) -> None:
        nonlocal calls
        _ = src
        _ = dst
        calls += 1
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    def copy2_with_partial_write(src: str, dst: str) -> None:
        _ = src
        Path(dst).write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(file_operations.os, "replace", replace_with_cross_device_error)
    monkeypatch.setattr(file_operations.shutil, "copy2", copy2_with_partial_write)

    with pytest.raises(OSError, match="No space left on device"):
        atomic_move_file(source=source, destination=destination)

    assert calls == 1
    assert source.read_bytes() == b"complete source payload"
    assert not destination.exists()
    assert list(destination.parent.glob("destination.bin.tmp.*")) == []


@pytest.mark.unit
def test_safe_unlink_file_missing_required_path_logs_and_raises(
    caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
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
def test_ensure_directory_and_safe_rmtree_emit_structured_events(
    caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
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
def test_safe_rmtree_retries_directory_not_empty_race(
    monkeypatch: MonkeyPatch, caplog: LogCaptureFixture, tmp_path: Path
) -> None:
    caplog.set_level(logging.INFO, logger="endoreg_db.utils.file_operations")
    target = tmp_path / "racy"
    ensure_directory(target)
    atomic_write_file(destination=target / "child.txt", content=(b"payload",))
    original_rmtree = file_operations.shutil.rmtree
    calls = 0

    def racy_rmtree(path: str | Path) -> None:
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
