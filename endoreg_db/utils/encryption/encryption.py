from __future__ import annotations

import base64
import io
import json
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator
from collections.abc import Buffer
from cryptography.exceptions import InvalidTag
from endoreg_db.utils.rust_backend import parse_encrypted_header
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from endoreg_db.config.secret_keyring import (
    configured_master_keys,
)

MAGIC = b"LXENC01\n"
HEADER_LENGTH_STRUCT = struct.Struct(">I")
CHUNK_LENGTH_STRUCT = struct.Struct(">I")
DEFAULT_CHUNK_SIZE = 1024 * 1024
DEK_SIZE = 32
NONCE_PREFIX_SIZE = 8
WRAP_NONCE_SIZE = 12
CHUNK_COUNTER_SIZE = 4
WRAP_AAD = b"lx-annotate:dek-wrap:v1"
MAX_HEADER_SIZE = 64 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024


def _require_cryptography():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Encrypted storage requires the 'cryptography' package to be installed."
        ) from exc
    return AESGCM


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def load_master_read_keys() -> tuple[bytes, ...]:
    try:
        ring = configured_master_keys()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None
    if ring is None:
        raise RuntimeError("A configured master key is required for encrypted storage")
    return ring.readers


def load_master_key() -> bytes:
    return load_master_read_keys()[0]


def decrypt_wrapped_key(nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """Only explicitly enrolled retiring keys may authenticate existing material."""
    for key in load_master_read_keys():
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag:
            continue
    raise InvalidTag("No configured generation authenticated the wrapped key")


def select_file_master_key(header: EncryptedFileHeader) -> bytes:
    for key in load_master_read_keys():
        try:
            unwrap_file_dek(header, key)
            return key
        except InvalidTag:
            continue
    raise InvalidTag("No configured generation authenticated the file")


@dataclass(frozen=True)
class EncryptedFileHeader:
    version: int
    algorithm: str
    chunk_size: int
    wrapped_dek: bytes
    wrap_nonce: bytes
    nonce_prefix: bytes

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != 1
            or self.algorithm != "AESGCM-chunked-v1"
        ):
            raise ValueError("Unsupported encrypted file header version or algorithm")
        if (
            type(self.chunk_size) is not int
            or not 0 < self.chunk_size <= MAX_CHUNK_SIZE
        ):
            raise ValueError(
                "Encrypted chunk size exceeds the bounded streaming contract"
            )
        if (
            len(self.wrapped_dek) != DEK_SIZE + 16
            or len(self.wrap_nonce) != WRAP_NONCE_SIZE
            or len(self.nonce_prefix) != NONCE_PREFIX_SIZE
        ):
            raise ValueError("Malformed encrypted file key material")

    def to_bytes(self) -> bytes:
        payload = {
            "version": self.version,
            "algorithm": self.algorithm,
            "chunk_size": self.chunk_size,
            "wrapped_dek": _b64encode(self.wrapped_dek),
            "wrap_nonce": _b64encode(self.wrap_nonce),
            "nonce_prefix": _b64encode(self.nonce_prefix),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedFileHeader":
        version, algorithm, chunk_size, wrapped_dek, wrap_nonce, nonce_prefix = (
            parse_encrypted_header(data)
        )
        return cls(
            version, algorithm, chunk_size, wrapped_dek, wrap_nonce, nonce_prefix
        )


@dataclass(frozen=True)
class EncryptedChunkIndexEntry:
    counter: int
    ciphertext_offset: int
    ciphertext_length: int
    plaintext_offset: int
    plaintext_length: int


@dataclass(frozen=True)
class EncryptedFileLayout:
    """Constant-time description of the chunked encrypted file layout."""

    header: EncryptedFileHeader
    header_bytes: bytes
    data_offset: int
    plaintext_size: int
    chunk_count: int


def build_file_header(
    *, master_key: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> EncryptedFileHeader:
    AESGCM = _require_cryptography()
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    dek = os.urandom(DEK_SIZE)
    wrap_nonce = os.urandom(WRAP_NONCE_SIZE)
    wrapped_dek = AESGCM(master_key).encrypt(wrap_nonce, dek, WRAP_AAD)
    return EncryptedFileHeader(
        version=1,
        algorithm="AESGCM-chunked-v1",
        chunk_size=chunk_size,
        wrapped_dek=wrapped_dek,
        wrap_nonce=wrap_nonce,
        nonce_prefix=os.urandom(NONCE_PREFIX_SIZE),
    )


def unwrap_file_dek(header: EncryptedFileHeader, master_key: bytes) -> bytes:
    AESGCM = _require_cryptography()
    return AESGCM(master_key).decrypt(header.wrap_nonce, header.wrapped_dek, WRAP_AAD)


def write_header(stream: BinaryIO, header: EncryptedFileHeader) -> bytes:
    encoded = header.to_bytes()
    stream.write(MAGIC)
    stream.write(HEADER_LENGTH_STRUCT.pack(len(encoded)))
    stream.write(encoded)
    return encoded


def _read_up_to(source: BinaryIO, size: int) -> bytes:
    """Fill bounded records even when a stream returns short reads."""
    parts = bytearray()
    while len(parts) < size:
        chunk = source.read(size - len(parts))
        if not chunk:
            break
        parts.extend(chunk)
    return bytes(parts)


def read_header(stream: BinaryIO) -> tuple[EncryptedFileHeader, bytes]:
    magic = _read_up_to(stream, len(MAGIC))
    if magic != MAGIC:
        raise ValueError("Unsupported encrypted file format")
    header_length_bytes = _read_up_to(stream, HEADER_LENGTH_STRUCT.size)
    if len(header_length_bytes) != HEADER_LENGTH_STRUCT.size:
        raise ValueError("Encrypted file header length is truncated")
    (header_length,) = HEADER_LENGTH_STRUCT.unpack(header_length_bytes)
    if not 0 < header_length <= MAX_HEADER_SIZE:
        raise ValueError("Encrypted file header exceeds its size limit")
    encoded = _read_up_to(stream, header_length)
    if len(encoded) != header_length:
        raise ValueError("Encrypted file header is truncated")
    return EncryptedFileHeader.from_bytes(encoded), encoded


def encrypt_stream(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    master_key: bytes,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> EncryptedFileHeader:
    AESGCM = _require_cryptography()
    header = build_file_header(master_key=master_key, chunk_size=chunk_size)
    header_bytes = write_header(destination, header)
    dek = unwrap_file_dek(header, master_key)
    cipher = AESGCM(dek)
    counter = 0

    while True:
        chunk = _read_up_to(source, chunk_size)
        if not chunk:
            break
        nonce = header.nonce_prefix + counter.to_bytes(CHUNK_COUNTER_SIZE, "big")
        ciphertext = cipher.encrypt(nonce, chunk, header_bytes)
        destination.write(CHUNK_LENGTH_STRUCT.pack(len(ciphertext)))
        destination.write(ciphertext)
        counter += 1

    return header


def iter_decrypted_chunks(
    source: BinaryIO,
    *,
    master_key: bytes,
) -> Iterator[bytes]:
    AESGCM = _require_cryptography()
    header, header_bytes = read_header(source)
    dek = unwrap_file_dek(header, master_key)
    cipher = AESGCM(dek)
    counter = 0
    final_chunk_seen = False

    while True:
        chunk_length_bytes = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
        if not chunk_length_bytes:
            return
        if len(chunk_length_bytes) != CHUNK_LENGTH_STRUCT.size:
            raise ValueError("Encrypted chunk length is truncated")
        (chunk_length,) = CHUNK_LENGTH_STRUCT.unpack(chunk_length_bytes)
        if final_chunk_seen or not 16 < chunk_length <= header.chunk_size + 16:
            raise ValueError("Encrypted chunk length exceeds its declared size")
        final_chunk_seen = chunk_length < header.chunk_size + 16
        ciphertext = _read_up_to(source, chunk_length)
        if len(ciphertext) != chunk_length:
            raise ValueError("Encrypted chunk payload is truncated")
        nonce = header.nonce_prefix + counter.to_bytes(CHUNK_COUNTER_SIZE, "big")
        yield cipher.decrypt(nonce, ciphertext, header_bytes)
        counter += 1


def build_chunk_index(
    source: BinaryIO,
) -> tuple[EncryptedFileHeader, bytes, list[EncryptedChunkIndexEntry], int]:
    layout = inspect_encrypted_file_layout(source)
    header = layout.header
    index: list[EncryptedChunkIndexEntry] = []
    full_record_size = CHUNK_LENGTH_STRUCT.size + header.chunk_size + 16
    for counter in range(layout.chunk_count):
        plaintext_offset = counter * header.chunk_size
        plaintext_length = min(
            header.chunk_size, layout.plaintext_size - plaintext_offset
        )
        source.seek(layout.data_offset + counter * full_record_size)
        length = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
        if len(length) != CHUNK_LENGTH_STRUCT.size:
            raise ValueError("Encrypted chunk length is truncated")
        (ciphertext_length,) = CHUNK_LENGTH_STRUCT.unpack(length)
        if ciphertext_length != plaintext_length + 16:
            raise ValueError("Encrypted chunk length does not match chunk geometry")
        index.append(
            EncryptedChunkIndexEntry(
                counter,
                source.tell(),
                ciphertext_length,
                plaintext_offset,
                plaintext_length,
            )
        )
    return header, layout.header_bytes, index, layout.plaintext_size


def inspect_encrypted_file_layout(source: BinaryIO) -> EncryptedFileLayout:
    """Inspect size and chunk geometry without walking every encrypted chunk."""
    header, header_bytes = read_header(source)
    if header.chunk_size <= 0:
        raise ValueError("Encrypted file chunk size must be positive")

    data_offset = source.tell()
    source.seek(0, io.SEEK_END)
    encrypted_size = source.tell()
    payload_size = encrypted_size - data_offset
    if payload_size < 0:
        raise ValueError("Encrypted file payload size is invalid")
    if payload_size == 0:
        return EncryptedFileLayout(
            header=header,
            header_bytes=header_bytes,
            data_offset=data_offset,
            plaintext_size=0,
            chunk_count=0,
        )

    authentication_tag_size = 16
    full_ciphertext_size = header.chunk_size + authentication_tag_size
    full_record_size = CHUNK_LENGTH_STRUCT.size + full_ciphertext_size
    full_chunk_count, final_record_size = divmod(payload_size, full_record_size)
    plaintext_size = full_chunk_count * header.chunk_size
    chunk_count = full_chunk_count

    if final_record_size:
        minimum_record_size = CHUNK_LENGTH_STRUCT.size + authentication_tag_size
        if final_record_size <= minimum_record_size:
            raise ValueError("Encrypted final chunk record is truncated")
        final_record_offset = data_offset + full_chunk_count * full_record_size
        source.seek(final_record_offset)
        length_bytes = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
        if len(length_bytes) != CHUNK_LENGTH_STRUCT.size:
            raise ValueError("Encrypted final chunk length is truncated")
        (ciphertext_length,) = CHUNK_LENGTH_STRUCT.unpack(length_bytes)
        if ciphertext_length + CHUNK_LENGTH_STRUCT.size != final_record_size:
            raise ValueError("Encrypted final chunk length does not match file size")
        final_plaintext_size = ciphertext_length - authentication_tag_size
        if final_plaintext_size < 0 or final_plaintext_size > header.chunk_size:
            raise ValueError("Encrypted final chunk payload is invalid")
        plaintext_size += final_plaintext_size
        chunk_count += 1

    return EncryptedFileLayout(
        header=header,
        header_bytes=header_bytes,
        data_offset=data_offset,
        plaintext_size=plaintext_size,
        chunk_count=chunk_count,
    )


def iter_decrypted_byte_range(
    source: BinaryIO,
    *,
    master_key: bytes,
    start: int,
    end: int,
    output_chunk_size: int = 64 * 1024,
    layout: EncryptedFileLayout | None = None,
) -> Iterator[bytes]:
    if start < 0 or end < start:
        raise ValueError(f"Invalid byte range: {start}-{end}")

    AESGCM = _require_cryptography()
    if output_chunk_size <= 0:
        raise ValueError("output_chunk_size must be positive")

    file_layout = layout or inspect_encrypted_file_layout(source)
    if end >= file_layout.plaintext_size:
        raise ValueError(
            f"Requested byte range {start}-{end} exceeds plaintext size "
            f"{file_layout.plaintext_size}"
        )

    header = file_layout.header
    header_bytes = file_layout.header_bytes
    dek = unwrap_file_dek(header, master_key)
    cipher = AESGCM(dek)
    authentication_tag_size = 16
    full_ciphertext_size = header.chunk_size + authentication_tag_size
    full_record_size = CHUNK_LENGTH_STRUCT.size + full_ciphertext_size
    first_counter = start // header.chunk_size
    last_counter = end // header.chunk_size

    for counter in range(first_counter, last_counter + 1):
        plaintext_offset = counter * header.chunk_size
        plaintext_length = min(
            header.chunk_size,
            file_layout.plaintext_size - plaintext_offset,
        )
        expected_ciphertext_length = plaintext_length + authentication_tag_size
        record_offset = file_layout.data_offset + counter * full_record_size
        source.seek(record_offset)
        length_bytes = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
        if len(length_bytes) != CHUNK_LENGTH_STRUCT.size:
            raise ValueError("Encrypted chunk length is truncated")
        (ciphertext_length,) = CHUNK_LENGTH_STRUCT.unpack(length_bytes)
        if ciphertext_length != expected_ciphertext_length:
            raise ValueError("Encrypted chunk length does not match chunk geometry")
        ciphertext = _read_up_to(source, ciphertext_length)
        if len(ciphertext) != ciphertext_length:
            raise ValueError("Encrypted chunk payload is truncated")
        nonce = header.nonce_prefix + counter.to_bytes(CHUNK_COUNTER_SIZE, "big")
        plaintext = cipher.decrypt(nonce, ciphertext, header_bytes)

        slice_start = max(start - plaintext_offset, 0)
        slice_end = min(end - plaintext_offset + 1, plaintext_length)
        selected = plaintext[slice_start:slice_end]
        for offset in range(0, len(selected), output_chunk_size):
            yield selected[offset : offset + output_chunk_size]


class DecryptedStream(io.RawIOBase):
    def __init__(self, source: BinaryIO, *, master_key: bytes):
        self._source = source
        self._chunks = iter_decrypted_chunks(source, master_key=master_key)
        self._buffer = bytearray()
        self._closed = False

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._source.close()
        super().close()

    def readinto(self, b: Buffer) -> int | None:
        if self._closed:
            return 0
        target = memoryview(b)
        while len(self._buffer) < len(target):
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration:
                break
        if not self._buffer:
            return 0
        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        del self._buffer[:size]
        return size
