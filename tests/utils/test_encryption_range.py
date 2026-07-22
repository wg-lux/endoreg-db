from __future__ import annotations

from io import BytesIO

import pytest

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
