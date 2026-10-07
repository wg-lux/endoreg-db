from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)
from endoreg_db.utils.encryption import storage_materialization
from endoreg_db.utils.paths import get_runtime_paths

pytestmark = pytest.mark.django_db


class _EncryptedStorage:
    def __init__(self, payload: bytes, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail

    def get_plaintext_size(self, name: str) -> int:
        return len(self.payload)

    def iter_decrypted_range(
        self, name: str, *, start: int, end: int, chunk_size: int
    ) -> list[bytes]:
        if self.fail:
            raise ValueError("authentication failed")
        return [self.payload[start : end + 1]]


class FieldFile:
    # Override fails if used in prod
    name = "model_weights/model.safetensors"

    def __init__(self, storage: _EncryptedStorage) -> None:
        self.storage = storage


def test_materialization_returns_plaintext_and_removes_temporary_file() -> None:
    field_file = FieldFile(_EncryptedStorage(b"plaintext weights"))

    with materialized_plaintext_field_file(field_file) as path:
        assert path.read_bytes() == b"plaintext weights"
        assert path.exists()
        assert path.parent == get_runtime_paths().transcoding.resolve()
        assert path.stat().st_mode & 0o777 == 0o600

    assert not path.exists()


def test_materialization_cleans_up_when_authenticated_read_fails() -> None:
    field_file = FieldFile(_EncryptedStorage(b"ciphertext", fail=True))
    root = get_runtime_paths().transcoding
    before = set(root.iterdir())

    with pytest.raises(ValueError, match="authentication failed"):
        with materialized_plaintext_field_file(field_file):
            pytest.fail("decryption should fail before yielding a plaintext path")

    assert set(root.iterdir()) == before


def test_materialization_removes_plaintext_when_consumer_fails() -> None:
    path: Path | None = None
    with pytest.raises(RuntimeError, match="consumer failed"):
        with materialized_plaintext_field_file(
            FieldFile(_EncryptedStorage(b"private"))
        ) as path:
            raise RuntimeError("consumer failed")
    assert path is not None and not path.exists()


def test_materialization_rejects_unprotected_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="protected transcoding"):
        with materialized_plaintext_field_file(
            FieldFile(_EncryptedStorage(b"private")), directory=tmp_path
        ):
            pytest.fail("unprotected destination accepted")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("prefix,suffix", [("../", ""), ("", "/escape")])
def test_materialization_rejects_filename_path_escape(prefix: str, suffix: str) -> None:
    with pytest.raises(ValueError, match="path separators"):
        with materialized_plaintext_field_file(
            FieldFile(_EncryptedStorage(b"private")), prefix=prefix, suffix=suffix
        ):
            pytest.fail("path escape accepted")


def test_materialization_collision_preserves_existing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = UUID(int=0)
    monkeypatch.setattr(storage_materialization, "uuid4", lambda: identifier)
    path = get_runtime_paths().transcoding / f"endoreg-fieldfile-{identifier.hex}"
    path.write_bytes(b"another owner")
    try:
        with pytest.raises(FileExistsError):
            with materialized_plaintext_field_file(
                FieldFile(_EncryptedStorage(b"private"))
            ):
                pytest.fail("existing destination accepted")
        assert path.read_bytes() == b"another owner"
    finally:
        path.unlink(missing_ok=True)
