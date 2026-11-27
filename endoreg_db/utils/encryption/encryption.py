from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO
from collections.abc import Iterable, Iterator
from collections.abc import Buffer
from cryptography.exceptions import InvalidTag
from endoreg_db.utils.rust_backend import (
    encrypted_chunk_context,
    parse_encrypted_header_metadata,
    require_encrypted_format_runtime,
)
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
WRAP_AAD = b"lx-annotate:dek-wrap:v1"
MAX_HEADER_SIZE = 64 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024
AUTHENTICATION_TAG_SIZE = 16

# Version two authenticates the final chunk (whole-chunk truncation and
# extension fail), selects the wrapping key by identifier instead of trial
# decryption, and binds the wrapped data key to an authenticated file identifier.
# Writers emit version two; version one remains readable for existing media.
FORMAT_VERSION = 2
ALGORITHM_V1 = "AESGCM-chunked-v1"
ALGORITHM_V2 = "AESGCM-chunked-v2"
WRAP_AAD_V2_PREFIX = b"lx-annotate:dek-wrap:v2"
KEY_ID_DOMAIN = b"lx-annotate:master-key-id:v2\x00"
KEY_ID_SIZE = 16
FILE_ID_SIZE = 16
_SUPPORTED_CONTRACTS = {(1, ALGORITHM_V1), (2, ALGORITHM_V2)}


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


def master_key_id(master_key: bytes) -> bytes:
    """Non-secret identifier of a master key: a domain-separated SHA-256 prefix."""
    if len(master_key) not in {16, 24, 32}:
        raise ValueError("master_key must be 16, 24, or 32 bytes for AES-GCM.")
    return hashlib.sha256(KEY_ID_DOMAIN + master_key).digest()[:KEY_ID_SIZE]


def select_file_master_key(header: EncryptedFileHeader) -> bytes:
    keys = load_master_read_keys()
    if header.key_id is not None:
        # Version two names its key; never fall back to trying other generations.
        for key in keys:
            if master_key_id(key) == header.key_id:
                unwrap_file_dek(header, key)
                return key
        raise InvalidTag("No configured generation authenticated the file")
    for key in keys:
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
    key_id: bytes | None = None
    file_id: bytes | None = None

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or (self.version, self.algorithm) not in _SUPPORTED_CONTRACTS
        ):
            raise ValueError("Unsupported encrypted file header version or algorithm")
        if self.version == 2:
            if (
                self.key_id is None
                or self.file_id is None
                or len(self.key_id) != KEY_ID_SIZE
                or len(self.file_id) != FILE_ID_SIZE
            ):
                raise ValueError("Encrypted file header identifiers are malformed")
        elif self.key_id is not None or self.file_id is not None:
            raise ValueError("Version one encrypted headers carry no identifiers")
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

    @property
    def authenticates_end(self) -> bool:
        """Version two requires an authenticated terminal chunk."""
        return self.version == 2

    def to_bytes(self) -> bytes:
        payload: dict[str, object] = {
            "version": self.version,
            "algorithm": self.algorithm,
            "chunk_size": self.chunk_size,
            "wrapped_dek": _b64encode(self.wrapped_dek),
            "wrap_nonce": _b64encode(self.wrap_nonce),
            "nonce_prefix": _b64encode(self.nonce_prefix),
        }
        if self.key_id is not None and self.file_id is not None:
            payload["key_id"] = _b64encode(self.key_id)
            payload["file_id"] = _b64encode(self.file_id)
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedFileHeader":
        (
            version,
            algorithm,
            chunk_size,
            wrapped_dek,
            wrap_nonce,
            nonce_prefix,
            key_id,
            file_id,
        ) = parse_encrypted_header_metadata(data)
        return cls(
            version,
            algorithm,
            chunk_size,
            wrapped_dek,
            wrap_nonce,
            nonce_prefix,
            key_id,
            file_id,
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


def _wrap_aad(header: EncryptedFileHeader) -> bytes:
    if header.key_id is None or header.file_id is None:
        return WRAP_AAD
    return WRAP_AAD_V2_PREFIX + header.key_id + header.file_id


def build_file_header(
    *, master_key: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> EncryptedFileHeader:
    """Create a version-two header with a fresh data key and file identifier."""
    require_encrypted_format_runtime()
    AESGCM = _require_cryptography()
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    key_id = master_key_id(master_key)
    file_id = os.urandom(FILE_ID_SIZE)
    dek = os.urandom(DEK_SIZE)
    wrap_nonce = os.urandom(WRAP_NONCE_SIZE)
    wrapped_dek = AESGCM(master_key).encrypt(
        wrap_nonce, dek, WRAP_AAD_V2_PREFIX + key_id + file_id
    )
    return EncryptedFileHeader(
        version=FORMAT_VERSION,
        algorithm=ALGORITHM_V2,
        chunk_size=chunk_size,
        wrapped_dek=wrapped_dek,
        wrap_nonce=wrap_nonce,
        nonce_prefix=os.urandom(NONCE_PREFIX_SIZE),
        key_id=key_id,
        file_id=file_id,
    )


def unwrap_file_dek(header: EncryptedFileHeader, master_key: bytes) -> bytes:
    AESGCM = _require_cryptography()
    if header.key_id is not None and master_key_id(master_key) != header.key_id:
        raise InvalidTag("The master key does not match the file's key identifier")
    return AESGCM(master_key).decrypt(
        header.wrap_nonce, header.wrapped_dek, _wrap_aad(header)
    )


def encrypt_chunks(
    header: EncryptedFileHeader,
    header_bytes: bytes,
    dek: bytes,
    chunks: Iterable[bytes],
) -> Iterator[bytes]:
    """Yield length-prefixed chunk records for plaintext split at chunk_size.

    Only the last chunk may be shorter. One chunk of look-ahead marks the final
    chunk; a version-two empty input produces one empty terminal chunk.
    """
    AESGCM = _require_cryptography()
    cipher = AESGCM(dek)
    iterator = iter(chunks)
    current = next(iterator, None)
    if current is None:
        if not header.authenticates_end:
            return
        current = b""
    counter = 0
    while current is not None:
        following = next(iterator, None)
        final = following is None
        nonce, aad = encrypted_chunk_context(
            header_bytes, counter, len(current) + AUTHENTICATION_TAG_SIZE, final
        )
        ciphertext = cipher.encrypt(nonce, current, aad)
        yield CHUNK_LENGTH_STRUCT.pack(len(ciphertext)) + ciphertext
        counter += 1
        current = following


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


def _iter_plaintext_chunks(source: BinaryIO, chunk_size: int) -> Iterator[bytes]:
    while True:
        chunk = _read_up_to(source, chunk_size)
        if not chunk:
            return
        yield chunk
        if len(chunk) < chunk_size:
            return


def encrypt_stream(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    master_key: bytes,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> EncryptedFileHeader:
    header = build_file_header(master_key=master_key, chunk_size=chunk_size)
    header_bytes = write_header(destination, header)
    dek = unwrap_file_dek(header, master_key)
    for record in encrypt_chunks(
        header, header_bytes, dek, _iter_plaintext_chunks(source, chunk_size)
    ):
        destination.write(record)
    return header


def _read_chunk_length(source: BinaryIO) -> int | None:
    length_bytes = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
    if not length_bytes:
        return None
    if len(length_bytes) != CHUNK_LENGTH_STRUCT.size:
        raise ValueError("Encrypted chunk length is truncated")
    (length,) = CHUNK_LENGTH_STRUCT.unpack(length_bytes)
    return length


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
    # Read one length field ahead so each chunk knows whether it is final.
    pending = _read_chunk_length(source)
    if pending is None and header.authenticates_end:
        raise ValueError("Encrypted file lacks its authenticated terminal chunk")

    maximum = header.chunk_size + AUTHENTICATION_TAG_SIZE
    if pending is not None and pending > maximum:
        raise ValueError("Encrypted chunk length exceeds its declared size")
    while pending is not None:
        ciphertext = _read_up_to(source, pending)
        if len(ciphertext) != pending:
            raise ValueError("Encrypted chunk payload is truncated")
        following = _read_chunk_length(source)
        # Reject an unbounded next length before it can change this chunk's role.
        if following is not None and following > maximum:
            raise ValueError("Encrypted chunk length exceeds its declared size")
        final = following is None
        nonce, aad = encrypted_chunk_context(
            header_bytes, counter, len(ciphertext), final
        )
        yield cipher.decrypt(nonce, ciphertext, aad)
        counter += 1
        pending = following


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
        if header.authenticates_end:
            raise ValueError("Encrypted file lacks its authenticated terminal chunk")
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
        if final_record_size < minimum_record_size:
            raise ValueError("Encrypted final chunk record is truncated")
        final_record_offset = data_offset + full_chunk_count * full_record_size
        source.seek(final_record_offset)
        length_bytes = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
        if len(length_bytes) != CHUNK_LENGTH_STRUCT.size:
            raise ValueError("Encrypted final chunk length is truncated")
        (ciphertext_length,) = CHUNK_LENGTH_STRUCT.unpack(length_bytes)
        if ciphertext_length + CHUNK_LENGTH_STRUCT.size != final_record_size:
            raise ValueError("Encrypted final chunk length does not match file size")
        encrypted_chunk_context(header_bytes, full_chunk_count, ciphertext_length, True)
        plaintext_size += ciphertext_length - authentication_tag_size
        chunk_count += 1

    return EncryptedFileLayout(
        header=header,
        header_bytes=header_bytes,
        data_offset=data_offset,
        plaintext_size=plaintext_size,
        chunk_count=chunk_count,
    )


def _decrypt_record(
    source: BinaryIO,
    layout: EncryptedFileLayout,
    cipher: AESGCM,
    counter: int,
) -> bytes:
    header = layout.header
    final = counter == layout.chunk_count - 1
    plaintext_offset = counter * header.chunk_size
    plaintext_length = min(header.chunk_size, layout.plaintext_size - plaintext_offset)
    full_record_size = (
        CHUNK_LENGTH_STRUCT.size + header.chunk_size + AUTHENTICATION_TAG_SIZE
    )
    source.seek(layout.data_offset + counter * full_record_size)
    length_bytes = _read_up_to(source, CHUNK_LENGTH_STRUCT.size)
    if len(length_bytes) != CHUNK_LENGTH_STRUCT.size:
        raise ValueError("Encrypted chunk length is truncated")
    (ciphertext_length,) = CHUNK_LENGTH_STRUCT.unpack(length_bytes)
    nonce, aad = encrypted_chunk_context(
        layout.header_bytes, counter, ciphertext_length, final
    )
    if ciphertext_length != plaintext_length + AUTHENTICATION_TAG_SIZE:
        raise ValueError("Encrypted chunk length does not match chunk geometry")
    ciphertext = _read_up_to(source, ciphertext_length)
    if len(ciphertext) != ciphertext_length:
        raise ValueError("Encrypted chunk payload is truncated")
    return cipher.decrypt(nonce, ciphertext, aad)


def authenticate_terminal_chunk(
    source: BinaryIO,
    *,
    master_key: bytes,
    layout: EncryptedFileLayout,
) -> None:
    """Authenticate the final chunk so the reported size cannot hide truncation.

    Version one has no authenticated end; callers need an independent reference.
    """
    if not layout.header.authenticates_end:
        return
    AESGCM = _require_cryptography()
    cipher = AESGCM(unwrap_file_dek(layout.header, master_key))
    _decrypt_record(source, layout, cipher, layout.chunk_count - 1)


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
    cipher = AESGCM(unwrap_file_dek(header, master_key))
    if header.version == 2:
        # Even an early range must reject a removed or altered terminal record.
        _decrypt_record(source, file_layout, cipher, file_layout.chunk_count - 1)
    first_counter = start // header.chunk_size
    last_counter = end // header.chunk_size

    for counter in range(first_counter, last_counter + 1):
        plaintext_offset = counter * header.chunk_size
        plaintext = _decrypt_record(source, file_layout, cipher, counter)
        slice_start = max(start - plaintext_offset, 0)
        slice_end = min(end - plaintext_offset + 1, len(plaintext))
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
