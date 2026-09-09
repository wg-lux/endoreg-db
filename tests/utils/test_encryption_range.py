from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from endoreg_db.utils.encryption import encrypted as encrypted_storage_module
from endoreg_db.utils.encryption.encrypted import (
    NATIVE_DECRYPT_BATCH_BYTES,
    EncryptedStorage,
)
from endoreg_db.utils.encryption.encryption import (
    encrypt_stream,
    inspect_encrypted_file_layout,
    iter_decrypted_byte_range,
)


@pytest.mark.unit
@pytest.mark.parametrize("payload_size", [32, 37, 96, 101])
def test_encrypted_layout_and_range_reads_do_not_require_full_plaintext(
    payload_size: int,
) -> None:
    master_key = b"k" * 32
    plaintext = bytes(index % 251 for index in range(payload_size))
    encrypted = BytesIO()
    encrypt_stream(
        BytesIO(plaintext),
        encrypted,
        master_key=master_key,
        chunk_size=32,
    )

    encrypted.seek(0)
    layout = inspect_encrypted_file_layout(encrypted)
    assert layout.plaintext_size == payload_size
    assert layout.chunk_count == (payload_size + 31) // 32

    start = min(29, payload_size - 1)
    end = min(start + 9, payload_size - 1)
    encrypted.seek(0)
    selected = b"".join(
        iter_decrypted_byte_range(
            encrypted,
            master_key=master_key,
            start=start,
            end=end,
            output_chunk_size=3,
            layout=layout,
        )
    )

    assert selected == plaintext[start : end + 1]


@pytest.mark.unit
def test_encrypted_range_rejects_tampered_chunk_geometry() -> None:
    master_key = b"k" * 32
    encrypted = BytesIO()
    encrypt_stream(
        BytesIO(b"a" * 40),
        encrypted,
        master_key=master_key,
        chunk_size=32,
    )
    encrypted.seek(0)
    layout = inspect_encrypted_file_layout(encrypted)
    encrypted.seek(layout.data_offset)
    encrypted.write(b"\x00\x00\x00\x01")
    encrypted.seek(0)

    with pytest.raises(ValueError, match="chunk geometry"):
        b"".join(
            iter_decrypted_byte_range(
                encrypted,
                master_key=master_key,
                start=0,
                end=3,
                layout=layout,
            )
        )


def _write_encrypted_file(
    path: Path,
    *,
    plaintext: bytes,
    master_key: bytes,
    chunk_size: int,
) -> None:
    with path.open("wb") as destination:
        encrypt_stream(
            BytesIO(plaintext),
            destination,
            master_key=master_key,
            chunk_size=chunk_size,
        )


@pytest.mark.unit
def test_encrypted_storage_prefers_bounded_native_range_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_key = b"k" * 32
    plaintext = bytes(index % 251 for index in range(NATIVE_DECRYPT_BATCH_BYTES + 37))
    encrypted_path = tmp_path / "video.mp4"
    _write_encrypted_file(
        encrypted_path,
        plaintext=plaintext,
        master_key=master_key,
        chunk_size=1024 * 1024,
    )
    calls: list[tuple[Path, int, int]] = []

    def native_decrypt(
        *,
        path: Path,
        master_key: bytes,
        start: int,
        end: int,
    ) -> bytes:
        assert master_key == b"k" * 32
        calls.append((path, start, end))
        return plaintext[start : end + 1]

    monkeypatch.setattr(
        encrypted_storage_module,
        "decrypt_encrypted_file_range",
        native_decrypt,
    )
    storage = EncryptedStorage(location=tmp_path, master_key=master_key)

    selected = b"".join(
        storage.iter_decrypted_range(
            encrypted_path.name,
            start=0,
            end=len(plaintext) - 1,
            chunk_size=64 * 1024,
        )
    )

    assert selected == plaintext
    assert calls == [
        (encrypted_path, 0, NATIVE_DECRYPT_BATCH_BYTES - 1),
        (
            encrypted_path,
            NATIVE_DECRYPT_BATCH_BYTES,
            len(plaintext) - 1,
        ),
    ]


@pytest.mark.unit
def test_encrypted_storage_uses_python_reference_when_native_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_key = b"k" * 32
    plaintext = b"native-unavailable-fallback"
    encrypted_path = tmp_path / "video.mp4"
    _write_encrypted_file(
        encrypted_path,
        plaintext=plaintext,
        master_key=master_key,
        chunk_size=8,
    )

    def native_unavailable(
        *,
        path: Path,
        master_key: bytes,
        start: int,
        end: int,
    ) -> None:
        del path, master_key, start, end
        return None

    monkeypatch.setattr(
        encrypted_storage_module,
        "decrypt_encrypted_file_range",
        native_unavailable,
    )
    storage = EncryptedStorage(
        location=tmp_path,
        master_key=master_key,
        chunk_size=8,
    )

    selected = b"".join(
        storage.iter_decrypted_range(
            encrypted_path.name,
            start=3,
            end=20,
            chunk_size=4,
        )
    )

    assert selected == plaintext[3:21]
