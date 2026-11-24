"""LXENC version two: authenticated end, key selection by identifier, file binding."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from cryptography.exceptions import InvalidTag

from endoreg_db import endoreg_rust_backend as native
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.encryption.encryption import (
    CHUNK_LENGTH_STRUCT,
    EncryptedFileHeader,
    build_file_header,
    encrypt_stream,
    encrypt_chunks,
    iter_decrypted_byte_range,
    iter_decrypted_chunks,
    master_key_id,
    read_header,
    select_file_master_key,
    unwrap_file_dek,
)
from endoreg_db.utils.encryption.rotation import rotate_encrypted_file
from endoreg_db.utils.file_operations import atomic_write_file
from tests.helpers.legacy_encryption import legacy_v1_encrypted

pytestmark = pytest.mark.no_db

CHUNK = 32
KEY = b"k" * 32
SIZES = [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK]


def write_file(path: Path, payload: bytes) -> Path:
    return atomic_write_file(destination=path, content=[payload], file_mode=0o600)


def encrypted(payload: bytes, key: bytes = KEY) -> bytes:
    target = BytesIO()
    encrypt_stream(BytesIO(payload), target, master_key=key, chunk_size=CHUNK)
    return target.getvalue()


def records(data: bytes) -> tuple[bytes, list[bytes]]:
    """Split a file into its magic+header prefix and its chunk records."""
    source = BytesIO(data)
    read_header(source)
    prefix = data[: source.tell()]
    chunks: list[bytes] = []
    while length_bytes := source.read(CHUNK_LENGTH_STRUCT.size):
        (length,) = CHUNK_LENGTH_STRUCT.unpack(length_bytes)
        chunks.append(length_bytes + source.read(length))
    return prefix, chunks


def legacy_v1(payload: bytes, key: bytes = KEY) -> bytes:
    return legacy_v1_encrypted(payload, key, chunk_size=CHUNK)


def assert_every_reader_rejects(path: Path, data: bytes) -> None:
    with pytest.raises((ValueError, InvalidTag)):
        list(iter_decrypted_chunks(BytesIO(data), master_key=KEY))
    with pytest.raises((ValueError, OSError)):
        native.stable_file_identity(path, CHUNK, [KEY])
    with pytest.raises((ValueError, InvalidTag)):
        EncryptedStorage(location=path.parent, master_key=KEY).get_plaintext_size(
            path.name
        )


def test_writers_emit_version_two_with_key_and_file_identifiers() -> None:
    first, second = (read_header(BytesIO(encrypted(b"x")))[0] for _ in range(2))
    for header in (first, second):
        assert (header.version, header.algorithm) == (2, "AESGCM-chunked-v2")
        assert header.key_id == master_key_id(KEY)
        assert header.file_id is not None and len(header.file_id) == 16
    assert first.file_id != second.file_id


@pytest.mark.parametrize(
    "missing", ["_parse_encrypted_header_metadata", "_encrypted_chunk_context"]
)
def test_mismatched_native_runtime_fails_before_writing(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    from endoreg_db.utils import rust_backend

    monkeypatch.setattr(rust_backend, missing, None)
    target = BytesIO()
    with pytest.raises(RuntimeError, match="matching Python and native"):
        encrypt_stream(BytesIO(b"payload"), target, master_key=KEY)
    assert target.getvalue() == b""


def test_legacy_encoder_rejects_empty_record_instead_of_writing_unreadable_file() -> (
    None
):
    header, encoded = read_header(BytesIO(legacy_v1(b"")))
    dek = unwrap_file_dek(header, KEY)
    assert list(encrypt_chunks(header, encoded, dek, [])) == []
    with pytest.raises(ValueError, match="chunk geometry"):
        list(encrypt_chunks(header, encoded, dek, [b""]))


@pytest.mark.parametrize("size", SIZES)
def test_every_reader_agrees_on_version_two_content(tmp_path: Path, size: int) -> None:
    payload = bytes((value * 7) % 251 for value in range(size))
    data = encrypted(payload)
    path = write_file(tmp_path / "v2.media", data)
    storage = EncryptedStorage(location=tmp_path, master_key=KEY)

    assert b"".join(iter_decrypted_chunks(BytesIO(data), master_key=KEY)) == payload
    assert storage.get_plaintext_size(path.name) == size
    digest = native.stable_file_identity(path, CHUNK, [KEY])[2]
    assert digest == hashlib.sha256(payload).hexdigest()
    if size:
        assert (
            b"".join(
                iter_decrypted_byte_range(
                    BytesIO(data), master_key=KEY, start=0, end=size - 1
                )
            )
            == payload
        )
        assert native.decrypt_encrypted_file_range(path, KEY, 0, size - 1) == payload
        assert (
            b"".join(storage.iter_decrypted_range(path.name, start=0, end=size - 1))
            == payload
        )


def test_empty_version_two_file_is_one_authenticated_terminal_chunk() -> None:
    _, chunks = records(encrypted(b""))
    assert [len(chunk) for chunk in chunks] == [CHUNK_LENGTH_STRUCT.size + 16]


@pytest.mark.parametrize("size", [CHUNK + 1, 2 * CHUNK, 3 * CHUNK])
def test_removing_whole_trailing_chunks_is_detected(tmp_path: Path, size: int) -> None:
    prefix, chunks = records(encrypted(bytes(size)))
    truncated = prefix + b"".join(chunks[:-1])
    path = write_file(tmp_path / "truncated.media", truncated)

    assert_every_reader_rejects(path, truncated)
    # Native ranges authenticate the requested chunks; the new last one fails.
    new_end = (len(chunks) - 1) * CHUNK - 1
    with pytest.raises(ValueError):
        native.decrypt_encrypted_file_range(path, KEY, new_end, new_end)
    # Served ranges authenticate the end first, so even early chunks are refused.
    storage = EncryptedStorage(location=tmp_path, master_key=KEY)
    with pytest.raises((ValueError, InvalidTag)):
        list(storage.iter_decrypted_range(path.name, start=0, end=0))


def test_appending_a_chunk_after_the_terminal_chunk_is_detected(tmp_path: Path) -> None:
    prefix, chunks = records(encrypted(bytes(2 * CHUNK)))
    extended = prefix + b"".join(chunks) + chunks[0]
    path = write_file(tmp_path / "extended.media", extended)

    assert_every_reader_rejects(path, extended)
    with pytest.raises(ValueError):
        native.decrypt_encrypted_file_range(path, KEY, CHUNK, 2 * CHUNK - 1)


def test_header_without_terminal_chunk_is_rejected(tmp_path: Path) -> None:
    prefix, _ = records(encrypted(b""))
    path = write_file(tmp_path / "header-only.media", prefix)

    assert_every_reader_rejects(path, prefix)
    with pytest.raises(ValueError, match="terminal chunk"):
        native.decrypt_encrypted_file_range(path, KEY, 0, 0)


def test_wrapped_data_key_is_bound_to_its_file_identifier() -> None:
    donor = build_file_header(master_key=KEY, chunk_size=CHUNK)
    target = build_file_header(master_key=KEY, chunk_size=CHUNK)
    transplanted = EncryptedFileHeader(
        version=target.version,
        algorithm=target.algorithm,
        chunk_size=target.chunk_size,
        wrapped_dek=donor.wrapped_dek,
        wrap_nonce=donor.wrap_nonce,
        nonce_prefix=target.nonce_prefix,
        key_id=target.key_id,
        file_id=target.file_id,
    )
    unwrap_file_dek(donor, KEY)
    with pytest.raises(InvalidTag):
        unwrap_file_dek(transplanted, KEY)


def test_key_identifier_selects_the_generation_without_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active, retiring, unknown = b"a" * 32, b"r" * 32, b"u" * 32
    keys: dict[str, Path] = {}
    for name, value in (("active", active), ("retiring", retiring)):
        keys[name] = write_file(tmp_path / name, base64.urlsafe_b64encode(value))
    manifest = write_file(
        tmp_path / "keys.yml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "active": str(keys["active"]),
                "retiring": [str(keys["retiring"])],
            }
        ).encode(),
    )
    monkeypatch.setenv("LX_ANNOTATE_MASTER_KEYRING_FILE", str(manifest))

    for key in (active, retiring):
        header = read_header(BytesIO(encrypted(b"payload", key)))[0]
        assert select_file_master_key(header) == key
    foreign = read_header(BytesIO(encrypted(b"payload", unknown)))[0]
    with pytest.raises(InvalidTag):
        select_file_master_key(foreign)
    with pytest.raises(InvalidTag):
        unwrap_file_dek(foreign, active)


@pytest.mark.parametrize("size", [0, 1, CHUNK, 3 * CHUNK + 5])
def test_version_one_media_remains_readable(tmp_path: Path, size: int) -> None:
    payload = bytes(range(size % 256)) * (size // 256 + 1)
    payload = payload[:size]
    data = legacy_v1(payload)
    path = write_file(tmp_path / "v1.media", data)
    storage = EncryptedStorage(location=tmp_path, master_key=KEY)

    assert read_header(BytesIO(data))[0].version == 1
    assert b"".join(iter_decrypted_chunks(BytesIO(data), master_key=KEY)) == payload
    assert storage.get_plaintext_size(path.name) == size
    assert native.stable_file_identity(path, CHUNK, [KEY])[2] == (
        hashlib.sha256(payload).hexdigest()
    )
    if size:
        assert native.decrypt_encrypted_file_range(path, KEY, 0, size - 1) == payload


def test_rotation_rewrites_version_one_media_as_version_two(tmp_path: Path) -> None:
    payload = bytes(range(100))
    active, retiring = b"a" * 32, KEY
    path = write_file(tmp_path / "rotate.media", legacy_v1(payload, retiring))

    result = rotate_encrypted_file(
        path, active_key=active, retiring_keys=(retiring,), apply=True
    )

    assert result.changed and result.plaintext_size == len(payload)
    data = path.read_bytes()
    header = read_header(BytesIO(data))[0]
    assert header.version == 2 and header.key_id == master_key_id(active)
    assert b"".join(iter_decrypted_chunks(BytesIO(data), master_key=active)) == payload
    prefix, chunks = records(data)
    truncated = write_file(tmp_path / "rotated-truncated.media", prefix + chunks[0])
    with pytest.raises((ValueError, OSError)):
        native.stable_file_identity(truncated, CHUNK, [active])


@pytest.mark.parametrize("damage", ["truncate", "append", "terminal-tag"])
def test_early_ranges_reject_terminal_damage(tmp_path: Path, damage: str) -> None:
    prefix, chunks = records(encrypted(b"a" * (3 * CHUNK)))
    if damage == "truncate":
        chunks.pop()
    elif damage == "append":
        chunks.append(chunks[0])
    else:
        chunks[-1] = chunks[-1][:-1] + bytes([chunks[-1][-1] ^ 1])
    data = prefix + b"".join(chunks)
    path = write_file(tmp_path / "damaged.media", data)
    with pytest.raises((ValueError, InvalidTag)):
        next(iter_decrypted_byte_range(BytesIO(data), master_key=KEY, start=0, end=0))
    with pytest.raises(ValueError):
        native.decrypt_encrypted_file_range(path, KEY, 0, 0)
    storage = EncryptedStorage(location=tmp_path, master_key=KEY)
    with pytest.raises((ValueError, InvalidTag)):
        next(storage.iter_decrypted_range(path.name, start=0, end=0))


def test_storage_range_pins_generation_across_atomic_replacement(
    tmp_path: Path,
) -> None:
    # Cross the former native batch boundary to detect mixed generations.
    payload = b"a" * (4 * 1024 * 1024 + 37)
    original, replacement = BytesIO(), BytesIO()
    for target, plaintext in ((original, payload), (replacement, b"b" * len(payload))):
        encrypt_stream(
            BytesIO(plaintext), target, master_key=KEY, chunk_size=1024 * 1024
        )
    path = write_file(tmp_path / "pinned.media", original.getvalue())
    storage = EncryptedStorage(location=tmp_path, master_key=KEY)
    stream = storage.iter_decrypted_range(path.name, start=0, end=len(payload) - 1)
    first = next(stream)
    write_file(path, replacement.getvalue())
    assert first + b"".join(stream) == payload
    assert b"".join(storage.iter_decrypted_range(path.name, start=0, end=0)) == b"b"
