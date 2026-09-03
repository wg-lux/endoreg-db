from __future__ import annotations

from pathlib import Path

import pytest

from endoreg_db.utils.encryption.storage_materialization import (
    materialized_plaintext_field_file,
)


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

    assert not path.exists()


def test_materialization_cleans_up_when_authenticated_read_fails() -> None:
    field_file = FieldFile(_EncryptedStorage(b"ciphertext", fail=True))
    before = set(Path("/tmp").glob("endoreg-fieldfile-*"))

    with pytest.raises(ValueError, match="authentication failed"):
        with materialized_plaintext_field_file(field_file):
            pytest.fail("decryption should fail before yielding a plaintext path")

    assert set(Path("/tmp").glob("endoreg-fieldfile-*")) == before
