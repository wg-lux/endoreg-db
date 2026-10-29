from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from endoreg_db import endoreg_rust_backend as native
from endoreg_db.config.secret_keyring import configured_master_keys
from endoreg_db.utils.encryption.encryption import (
    CHUNK_LENGTH_STRUCT,
    HEADER_LENGTH_STRUCT,
    MAGIC,
    MAX_HEADER_SIZE,
    EncryptedFileHeader,
    build_file_header,
    build_chunk_index,
    encrypt_stream,
    iter_decrypted_chunks,
    iter_decrypted_byte_range,
    read_header,
    unwrap_file_dek,
)
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.rust_backend import stable_file_identity, stable_file_identities

pytestmark = pytest.mark.no_db


def write_file(path: Path, payload: bytes) -> Path:
    return atomic_write_file(destination=path, content=[payload], file_mode=0o600)


def encrypted(payload: bytes, key: bytes) -> bytes:
    target = BytesIO()
    encrypt_stream(BytesIO(payload), target, master_key=key, chunk_size=32)
    return target.getvalue()


def keyring(root: Path, active: bytes, retiring: bytes) -> Path:
    new = write_file(root / "active", base64.urlsafe_b64encode(active))
    old = write_file(root / "retiring", base64.urlsafe_b64encode(retiring))
    return write_file(
        root / "keys.yml",
        yaml.safe_dump(
            {"schema_version": 1, "active": str(new), "retiring": [str(old)]}
        ).encode(),
    )


@pytest.mark.parametrize("key_size", [16, 24, 32])
@pytest.mark.parametrize("payload_size", [0, 1, 32, 65])
def test_native_single_and_batch_hash_read_retiring_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key_size: int,
    payload_size: int,
) -> None:
    old, new = bytes(range(key_size)), b"n" * key_size
    manifest = keyring(tmp_path, new, old)
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEYRING_FILE", str(manifest))
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY", "invalid-shadowed-inline-key")
    payload = bytes(range(payload_size))
    old_file = write_file(tmp_path / "old.media", encrypted(payload, old))
    new_file = write_file(tmp_path / "new.media", encrypted(payload, new))
    results = stable_file_identities([old_file, new_file], worker_count=2)
    assert results is not None
    for path, result in zip([old_file, new_file], results, strict=True):
        assert stable_file_identity(path) == result
        assert result[2] == hashlib.sha256(payload).hexdigest()
    snapshot = configured_master_keys()
    assert snapshot is not None and snapshot.readers == (new, old)
    monkeypatch.delenv("LX_ANNOTATE_MASTER_KEYRING_FILE")
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY", base64.urlsafe_b64encode(new).decode())
    with pytest.raises(RuntimeError, match="authenticated"):
        stable_file_identity(old_file)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", "1"),
        ("version", 1.0),
        ("version", 2),
        ("chunk_size", True),
        ("chunk_size", "32"),
        ("chunk_size", 32.0),
        ("chunk_size", 0),
        ("chunk_size", -1),
        ("chunk_size", 64 * 1024 * 1024 + 1),
        ("algorithm", "unknown"),
        ("extra", 1),
        ("wrap_nonce", ""),
        ("nonce_prefix", "!!!"),
        ("wrapped_dek", "AA=="),
    ],
)
def test_all_readers_reject_the_same_invalid_header(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    key = b"k" * 32
    payload: dict[str, object] = json.loads(
        build_file_header(master_key=key, chunk_size=32).to_bytes()
    )
    payload[field] = value
    encoded = json.dumps(payload).encode()
    data = MAGIC + HEADER_LENGTH_STRUCT.pack(len(encoded)) + encoded
    path = write_file(tmp_path / "bad.media", data)
    with pytest.raises(ValueError):
        EncryptedFileHeader.from_bytes(encoded)
    with pytest.raises(ValueError):
        read_header(BytesIO(data))
    with pytest.raises(ValueError):
        native.decrypt_encrypted_file_range(path, key, 0, 0)
    with pytest.raises((ValueError, OSError)):
        native.stable_file_identity(path, 32, [key])


@pytest.mark.parametrize("padding,accepted", [(0, True), (1, False)])
def test_shared_header_size_limit(padding: int, accepted: bool) -> None:
    encoded = build_file_header(master_key=b"k" * 32).to_bytes()
    encoded += b" " * (MAX_HEADER_SIZE + padding - len(encoded))
    if accepted:
        assert EncryptedFileHeader.from_bytes(encoded).chunk_size == 1024 * 1024
    else:
        with pytest.raises(ValueError):
            EncryptedFileHeader.from_bytes(encoded)


@pytest.mark.parametrize("suffix", [b"\x00", b"\x00\x00", b"\x00\x00\x00", b"\xff" * 4])
def test_truncated_or_unbounded_chunk_lengths_fail_every_full_reader(
    tmp_path: Path,
    suffix: bytes,
) -> None:
    key = b"k" * 32
    data = encrypted(b"a" * 32, key) + suffix
    path = write_file(tmp_path / "truncated.media", data)
    with pytest.raises((ValueError, OSError)):
        native.stable_file_identity(path, 32, [key])
    with pytest.raises(ValueError):
        list(iter_decrypted_chunks(BytesIO(data), master_key=key))
    with pytest.raises(ValueError):
        build_chunk_index(BytesIO(data))


def test_authenticated_short_chunk_cannot_precede_another_chunk(tmp_path: Path) -> None:
    key = b"k" * 32
    header = build_file_header(master_key=key, chunk_size=32)
    encoded = header.to_bytes()
    cipher = AESGCM(unwrap_file_dek(header, key))
    data = MAGIC + HEADER_LENGTH_STRUCT.pack(len(encoded)) + encoded
    for counter in range(2):
        ciphertext = cipher.encrypt(
            header.nonce_prefix + counter.to_bytes(4, "big"), b"x", encoded
        )
        data += CHUNK_LENGTH_STRUCT.pack(len(ciphertext)) + ciphertext
    path = write_file(tmp_path / "bad-geometry.media", data)
    with pytest.raises((ValueError, OSError)):
        native.stable_file_identity(path, 32, [key])
    with pytest.raises(ValueError):
        list(iter_decrypted_chunks(BytesIO(data), master_key=key))


class ShortReader(BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        return super().read(min(size if size is not None and size >= 0 else 3, 3))


def test_short_reads_preserve_encryption_and_streaming_geometry() -> None:
    key = b"k" * 32
    payload = bytes(range(100))
    target = BytesIO()
    encrypt_stream(ShortReader(payload), target, master_key=key, chunk_size=32)
    data = target.getvalue()
    assert b"".join(iter_decrypted_chunks(ShortReader(data), master_key=key)) == payload
    assert (
        b"".join(
            iter_decrypted_byte_range(
                ShortReader(data), master_key=key, start=5, end=70
            )
        )
        == payload[5:71]
    )


def test_native_reader_never_loads_environment_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = b"k" * 32
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEY", base64.urlsafe_b64encode(key).decode())
    path = write_file(tmp_path / "encrypted.media", encrypted(b"content", key))
    with pytest.raises((ValueError, OSError), match="authenticated"):
        native.stable_file_identity(path)


def test_empty_encrypted_file_size_still_authenticates_key(tmp_path: Path) -> None:
    from cryptography.exceptions import InvalidTag
    from endoreg_db.utils.encryption.encrypted import EncryptedStorage

    key = b"k" * 32
    path = write_file(tmp_path / "empty.media", encrypted(b"", key))
    assert (
        EncryptedStorage(location=tmp_path, master_key=key).get_plaintext_size(
            path.name
        )
        == 0
    )
    with pytest.raises(InvalidTag):
        EncryptedStorage(location=tmp_path, master_key=b"w" * 32).get_plaintext_size(
            path.name
        )


def test_magic_probe_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="regular file"):
        native.encryption_status(tmp_path)


def test_duplicate_header_fields_are_rejected() -> None:
    encoded = build_file_header(master_key=b"k" * 32).to_bytes()
    duplicate = b'{"version":1,' + encoded[1:]
    with pytest.raises(ValueError):
        EncryptedFileHeader.from_bytes(duplicate)
