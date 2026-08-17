"""Fail-closed receiver boundary for site-to-hub media envelopes."""

from __future__ import annotations

import base64
import hashlib
import io
import stat
import uuid
from collections.abc import Buffer, Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from lx_dtypes.models.contracts.hub_media_envelope import HubMediaEnvelopeMetadata
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.hub.transfer_job import TransferJob
from endoreg_db.utils.file_operations import (
    atomic_handoff_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.paths import TRANSCODING_DIR

_CHUNK_SIZE = 1024 * 1024
_WRAP_INFO = b"lx-hub-media-envelope-wrap-v1"


class HubMediaEnvelopeError(ValueError):
    """An inbound envelope failed identity, key, or integrity validation."""


class HubMediaEnvelopeReplayConflict(HubMediaEnvelopeError):
    """An applied transfer received a different envelope or ciphertext."""


def _decode_base64url(value: str, *, expected_size: int, field_name: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (TypeError, ValueError) as exc:
        raise HubMediaEnvelopeError(f"{field_name} must be base64url encoded") from exc
    if len(decoded) != expected_size:
        raise HubMediaEnvelopeError(
            f"{field_name} must decode to exactly {expected_size} bytes"
        )
    return decoded


def _private_key_identifier(private_key: X25519PrivateKey) -> str:
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(public_bytes).hexdigest()


def _load_recipient_private_key(path: Path) -> X25519PrivateKey:
    if not path.is_absolute():
        raise HubMediaEnvelopeError("Hub envelope private key paths must be absolute")
    if path.is_symlink() or not path.is_file():
        raise HubMediaEnvelopeError(
            "Hub envelope private keys must be regular non-symlink files"
        )
    file_stat = path.stat()
    if (
        bool(
            getattr(
                settings,
                "ENDOREG_HUB_TRANSFER_REQUIRE_ROOT_OWNED_PRIVATE_KEYS",
                True,
            )
        )
        and file_stat.st_uid != 0
    ):
        raise HubMediaEnvelopeError(
            "Hub envelope private keys must be owned by the root account"
        )
    file_mode = stat.S_IMODE(file_stat.st_mode)
    if file_mode & 0o007 or file_mode & 0o020:
        raise HubMediaEnvelopeError(
            "Hub envelope private keys must not be accessible by other users "
            "or writable by the group"
        )
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (TypeError, ValueError, OSError) as exc:
        raise HubMediaEnvelopeError("Hub envelope private key is invalid") from exc
    if not isinstance(key, X25519PrivateKey):
        raise HubMediaEnvelopeError("Hub envelope private key must use X25519")
    return key


def _configured_recipient_private_keys() -> dict[str, X25519PrivateKey]:
    configured = cast(
        Sequence[str | Path],
        getattr(settings, "ENDOREG_HUB_TRANSFER_RECIPIENT_PRIVATE_KEY_FILES", ()),
    )
    if not configured:
        raise HubMediaEnvelopeError(
            "Hub media envelope recipient private key files are not configured"
        )
    keyring: dict[str, X25519PrivateKey] = {}
    for configured_path in configured:
        private_key = _load_recipient_private_key(Path(configured_path))
        key_identifier = _private_key_identifier(private_key)
        if key_identifier in keyring:
            raise HubMediaEnvelopeError(
                "Hub envelope recipient key identifiers must be unique"
            )
        keyring[key_identifier] = private_key
    return keyring


def _expected_processed_media_hash(transfer_job: TransferJob) -> str:
    resource_rows = cast(JsonObject, transfer_job.resource_rows)
    if transfer_job.resource_kind == TransferJob.ResourceKind.VIDEO.value:
        video_file = resource_rows.get("video_file")
        return (
            str(video_file.get("processed_video_hash") or "").strip()
            if isinstance(video_file, dict)
            else ""
        )
    raw_pdf_state = resource_rows.get("raw_pdf_state")
    return (
        str(raw_pdf_state.get("processed_file_sha256") or "").strip()
        if isinstance(raw_pdf_state, dict)
        else ""
    )


def validate_envelope_identity(
    metadata: HubMediaEnvelopeMetadata,
    *,
    transfer_job: TransferJob,
    media_role: str,
) -> None:
    source_center = transfer_job.source_center
    expected_processed_hash = _expected_processed_media_hash(transfer_job)
    expected = {
        "transfer_key": transfer_job.transfer_key,
        "source_node_key": transfer_job.source_node.node_key,
        "target_node_key": transfer_job.target_node.node_key,
        "source_center_key": (
            source_center.center_key if source_center is not None else ""
        ),
        "resource_kind": transfer_job.resource_kind,
        "resource_hash": transfer_job.resource_hash,
        "processed_media_hash": expected_processed_hash,
        "transfer_mode": transfer_job.transfer_mode,
        "media_role": media_role,
        "plaintext_sha256": expected_processed_hash,
    }
    mismatches = [
        field_name
        for field_name, expected_value in expected.items()
        if not expected_value
        or str(getattr(metadata, field_name, "") or "") != str(expected_value)
    ]
    if mismatches:
        raise HubMediaEnvelopeError(
            "Hub media envelope identity mismatch for: " + ", ".join(sorted(mismatches))
        )


class _AuthenticatedEnvelopeReader(io.RawIOBase):
    def __init__(
        self,
        *,
        ciphertext_path: Path,
        data_encryption_key: bytes,
        metadata: HubMediaEnvelopeMetadata,
    ) -> None:
        super().__init__()
        self._source = ciphertext_path.open("rb")
        self._metadata = metadata
        self._decryptor = Cipher(
            algorithms.AES(data_encryption_key),
            modes.GCM(
                _decode_base64url(
                    metadata.payload_nonce,
                    expected_size=12,
                    field_name="payload_nonce",
                ),
                _decode_base64url(
                    metadata.payload_tag,
                    expected_size=16,
                    field_name="payload_tag",
                ),
            ),
        ).decryptor()
        self._decryptor.authenticate_additional_data(metadata.authenticated_data())
        self._pending = bytearray()
        self._plaintext_digest = hashlib.sha256()
        self._plaintext_size = 0
        self._finalized = False
        self.verified = False

    def readable(self) -> bool:
        return True

    def _observe_plaintext(self, plaintext: bytes) -> None:
        self._plaintext_digest.update(plaintext)
        self._plaintext_size += len(plaintext)
        self._pending.extend(plaintext)

    def _read_next(self) -> None:
        ciphertext = self._source.read(_CHUNK_SIZE)
        if ciphertext:
            self._observe_plaintext(self._decryptor.update(ciphertext))
            return
        try:
            self._observe_plaintext(self._decryptor.finalize())
        except InvalidTag as exc:
            raise HubMediaEnvelopeError(
                "Hub media envelope authentication failed"
            ) from exc
        self._finalized = True
        if (
            self._plaintext_size != self._metadata.plaintext_size
            or self._plaintext_digest.hexdigest() != self._metadata.plaintext_sha256
        ):
            raise HubMediaEnvelopeError(
                "Hub media envelope plaintext digest or size mismatch"
            )
        self.verified = True

    def readinto(self, buffer: Buffer) -> int:
        buffer_view = memoryview(buffer)
        requested = buffer_view.nbytes
        while len(self._pending) < requested and not self._finalized:
            self._read_next()
        count = min(requested, len(self._pending))
        if count:
            buffer_view[:count] = self._pending[:count]
            del self._pending[:count]
        return count

    def close(self) -> None:
        self._source.close()
        super().close()


@dataclass
class PreparedInboundHubEnvelope:
    metadata: HubMediaEnvelopeMetadata
    plaintext_stream: BinaryIO
    ciphertext_sha256: str
    ciphertext_size: int
    envelope_fingerprint_sha256: str
    receiver_node_key: str
    _reader: _AuthenticatedEnvelopeReader
    replay_accepted: bool = False

    def require_verified(self) -> None:
        if not self._reader.verified and not self.replay_accepted:
            raise HubMediaEnvelopeError(
                "Hub media envelope plaintext stream was not fully authenticated"
            )

    def accept_exact_replay(self) -> None:
        self.replay_accepted = True


def _unwrap_data_encryption_key(
    metadata: HubMediaEnvelopeMetadata,
) -> bytes:
    keyring = _configured_recipient_private_keys()
    private_key = keyring.get(metadata.recipient_key_id)
    if private_key is None:
        raise HubMediaEnvelopeError(
            "Hub media envelope recipient key is not available on this receiver"
        )
    ephemeral_public_key = X25519PublicKey.from_public_bytes(
        _decode_base64url(
            metadata.ephemeral_public_key,
            expected_size=32,
            field_name="ephemeral_public_key",
        )
    )
    wrapping_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_decode_base64url(
            metadata.wrap_salt,
            expected_size=16,
            field_name="wrap_salt",
        ),
        info=_WRAP_INFO,
    ).derive(private_key.exchange(ephemeral_public_key))
    try:
        data_encryption_key = AESGCM(wrapping_key).decrypt(
            _decode_base64url(
                metadata.wrap_nonce,
                expected_size=12,
                field_name="wrap_nonce",
            ),
            _decode_base64url(
                metadata.wrapped_data_encryption_key,
                expected_size=48,
                field_name="wrapped_data_encryption_key",
            ),
            metadata.authenticated_data(),
        )
    except InvalidTag as exc:
        raise HubMediaEnvelopeError(
            "Hub media envelope data-encryption key authentication failed"
        ) from exc
    if len(data_encryption_key) != 32:
        raise HubMediaEnvelopeError(
            "Hub media envelope data-encryption key has an invalid length"
        )
    return data_encryption_key


def _uploaded_chunks(uploaded_file: UploadedFile) -> Iterator[bytes]:
    chunks = cast(Iterator[bytes], uploaded_file.chunks(chunk_size=_CHUNK_SIZE))
    for chunk in chunks:
        if chunk:
            yield bytes(chunk)


@contextmanager
def prepare_inbound_hub_envelope(
    *,
    transfer_job: TransferJob,
    uploaded_file: UploadedFile,
    envelope_json: str,
    media_role: str,
) -> Generator[PreparedInboundHubEnvelope]:
    try:
        metadata = HubMediaEnvelopeMetadata.model_validate_json(envelope_json)
    except ValueError as exc:
        raise HubMediaEnvelopeError("Hub media envelope metadata is invalid") from exc
    validate_envelope_identity(
        metadata,
        transfer_job=transfer_job,
        media_role=media_role,
    )
    uploaded_size = uploaded_file.size
    if (
        uploaded_size is None
        or uploaded_size <= 0
        or uploaded_size != metadata.plaintext_size
    ):
        raise HubMediaEnvelopeError(
            "Hub media envelope ciphertext size does not match declared plaintext size"
        )

    destination = TRANSCODING_DIR / f"hub-envelope-{uuid.uuid4().hex}.ciphertext"
    atomic_handoff_file(
        destination=destination,
        content=_uploaded_chunks(uploaded_file),
        required_bytes=uploaded_size,
        file_mode=0o600,
        dir_mode=0o700,
    )
    try:
        ciphertext_sha256 = sha256_file(destination)
        data_encryption_key = _unwrap_data_encryption_key(metadata)
        reader = _AuthenticatedEnvelopeReader(
            ciphertext_path=destination,
            data_encryption_key=data_encryption_key,
            metadata=metadata,
        )
        buffered_reader = io.BufferedReader(reader, buffer_size=_CHUNK_SIZE)
        prepared = PreparedInboundHubEnvelope(
            metadata=metadata,
            plaintext_stream=buffered_reader,
            ciphertext_sha256=ciphertext_sha256,
            ciphertext_size=uploaded_size,
            envelope_fingerprint_sha256=metadata.envelope_fingerprint_sha256(),
            receiver_node_key=transfer_job.target_node.node_key,
            _reader=reader,
        )
        try:
            yield prepared
            prepared.require_verified()
        finally:
            buffered_reader.close()
    finally:
        safe_unlink_file(destination, missing_ok=True)


__all__ = [
    "HubMediaEnvelopeError",
    "HubMediaEnvelopeReplayConflict",
    "PreparedInboundHubEnvelope",
    "prepare_inbound_hub_envelope",
    "validate_envelope_identity",
]
