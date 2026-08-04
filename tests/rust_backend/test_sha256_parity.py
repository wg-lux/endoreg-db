from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.system.rust_backend import sha256_file_hex as rust_sha256_file_hex


def _python_sha256_file(path: Path, chunk_size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while True:
            chunk = file_handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.parametrize("chunk_size", [1, 7, 4096, 1024 * 1024])
def test_sha256_rust_backend_matches_python_reference(
    tmp_path: Path, chunk_size: int
) -> None:
    test_file = tmp_path / f"sample_{chunk_size}.bin"
    test_file.write_bytes((b"0123456789abcdef" * 131072) + b"tail")

    expected = _python_sha256_file(test_file, chunk_size)

    assert sha256_file(test_file, chunk_size=chunk_size) == expected

    rust_digest = rust_sha256_file_hex(test_file, chunk_size)
    if rust_digest is not None:
        assert rust_digest == expected


def test_sha256_rust_backend_returns_none_and_preserves_python_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_file = tmp_path / "sample.bin"
    test_file.write_bytes(b"abc123")

    import endoreg_db.utils.system.rust_backend as rust_backend_module

    monkeypatch.setattr(
        rust_backend_module,
        "_sha256_file_hex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert rust_backend_module.sha256_file_hex(test_file, 1024) is None
    assert (
        sha256_file(test_file, chunk_size=1024) == hashlib.sha256(b"abc123").hexdigest()
    )
