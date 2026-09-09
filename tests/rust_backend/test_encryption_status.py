from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from endoreg_db.utils.encryption.encrypted import MAGIC
from endoreg_db.utils.rust_backend import (
    encryption_status,
    is_lx_encrypted_file,
)


def raise_encryption_status_error(*args: object, **kwargs: object) -> NoReturn:
    raise RuntimeError("boom")


def test_encryption_status_rust_backend_matches_magic_header(tmp_path: Path) -> None:
    encrypted = tmp_path / "encrypted.bin"
    plaintext = tmp_path / "plaintext.bin"
    encrypted.write_bytes(MAGIC + b"ciphertext")
    plaintext.write_bytes(b"\x00\x00\x00\x20ftypmp42")

    status = encryption_status(encrypted)
    if status is not None:
        assert status == "encrypted"

    plaintext_status = encryption_status(plaintext)
    if plaintext_status is not None:
        assert plaintext_status == "plaintext"

    encrypted_result = is_lx_encrypted_file(encrypted)
    if encrypted_result is not None:
        assert encrypted_result is True

    plaintext_result = is_lx_encrypted_file(plaintext)
    if plaintext_result is not None:
        assert plaintext_result is False


def test_encryption_status_returns_none_and_preserves_python_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    encrypted = tmp_path / "encrypted.bin"
    encrypted.write_bytes(MAGIC + b"ciphertext")

    import endoreg_db.utils.rust_backend as rust_backend_module

    monkeypatch.setattr(
        rust_backend_module,
        "_encryption_status",
        raise_encryption_status_error,
    )
    monkeypatch.setattr(
        rust_backend_module,
        "_is_lx_encrypted_file",
        raise_encryption_status_error,
    )

    assert rust_backend_module.encryption_status(encrypted) is None
    assert rust_backend_module.is_lx_encrypted_file(encrypted) is None
