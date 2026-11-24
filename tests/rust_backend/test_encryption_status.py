from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NoReturn

import pytest

from endoreg_db.utils.encryption.encryption import MAGIC
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.rust_backend import (
    encryption_status,
    is_lx_encrypted_file,
    stable_file_identity,
)

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("payload", [b"", b"LXENC01", b"plain text content" * 10])
def test_native_identity_probe_preserves_entire_plaintext(
    tmp_path: Path, payload: bytes
) -> None:
    path = atomic_write_file(destination=tmp_path / "media", content=[payload])
    identity = stable_file_identity(path)
    assert identity is not None
    size, _, digest = identity
    assert size == len(payload)
    assert digest == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (MAGIC, True),
        (MAGIC + b"payload", True),
        (b"", False),
        (MAGIC[:-1], False),
        (b"LXENC02\n", False),
        (b"\x00\x00\x00\x20ftypmp42", False),
    ],
)
def test_native_probe_classifies_complete_magic_only(
    tmp_path: Path,
    payload: bytes,
    expected: bool,
) -> None:
    path = atomic_write_file(destination=tmp_path / "media", content=[payload])
    assert encryption_status(path) == ("encrypted" if expected else "plaintext")
    assert is_lx_encrypted_file(path) is expected


def test_missing_file_raises_in_both_adapters(tmp_path: Path) -> None:
    for probe in (encryption_status, is_lx_encrypted_file):
        with pytest.raises(FileNotFoundError):
            probe(tmp_path / "missing")


def test_backend_absence_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import endoreg_db.utils.rust_backend as backend

    monkeypatch.setattr(backend, "_encryption_status", None)
    for probe in (encryption_status, is_lx_encrypted_file):
        with pytest.raises(RuntimeError, match="required"):
            probe(tmp_path / "media")


@pytest.mark.parametrize("failure", [PermissionError, RuntimeError])
def test_native_failures_are_not_plaintext(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: type[Exception],
) -> None:
    import endoreg_db.utils.rust_backend as backend

    def fail(path: Path) -> NoReturn:
        raise failure("probe failed")

    monkeypatch.setattr(backend, "_encryption_status", fail)
    for probe in (encryption_status, is_lx_encrypted_file):
        with pytest.raises(failure, match="probe failed"):
            probe(tmp_path / "media")


def test_invalid_native_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.utils.rust_backend as backend

    def invalid(path: Path) -> str:
        return "unknown"

    monkeypatch.setattr(backend, "_encryption_status", invalid)
    for probe in (encryption_status, is_lx_encrypted_file):
        with pytest.raises(ValueError, match="unsupported"):
            probe(tmp_path / "media")
