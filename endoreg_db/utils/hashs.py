from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.db.models.fields.files import FieldFile

from endoreg_db.config.identity_hashing import (
    current_identity_keyring,
    validate_identity_salt,
)
from endoreg_db.utils.rust_backend import stable_file_identity

logger = logging.getLogger(__name__)

type HubRecordKind = Literal["patient", "examination"]


def get_bytes_hash(payload: bytes) -> str:
    """Return an unkeyed SHA-256 digest of already serialized bytes.

    Callers own encoding and domain separation. Use get_file_hash for files
    and the keyed identity helpers below for patient or examiner identities.
    """
    return hashlib.sha256(payload).hexdigest()


def _get_active_salt_bytes(salt: bytes | str | None = None) -> bytes:
    """Resolve validated UTF-8 material or an enrolled historical generation."""
    ring = current_identity_keyring()
    if ring is not None:
        validate_identity_salt(ring.active)
    if salt is not None:
        candidate = salt.encode("utf-8") if isinstance(salt, str) else salt
        # Retained generations are read-only compatibility material, enrolled
        # through the private manifest rather than accepted as active settings.
        if ring is not None and candidate in ring.retiring:
            return candidate
        return validate_identity_salt(candidate).encode("utf-8")
    return validate_identity_salt(
        ring.active if ring is not None else getattr(settings, "DJANGO_SALT", None)
    ).encode("utf-8")


def get_identity_salt() -> str:
    """Retrieve validated identity salt string."""
    return _get_active_salt_bytes().decode("utf-8")


def get_identity_hash(payload: bytes, *, salt: bytes | str | None = None) -> str:
    """Hash a domain-separated identity payload with the validated identity salt."""
    return hmac.new(_get_active_salt_bytes(salt), payload, hashlib.sha256).hexdigest()


def get_center_key(center_identifier: str) -> str:
    """Resolve a Center instance by name or center_key and return its canonical center_key."""
    from endoreg_db.models.administration.center import Center

    center = Center.resolve_identity(center_identifier)
    if center is None or not center.center_key:
        raise ValueError(
            f"Could not resolve Center with identifier '{center_identifier}' to a valid center_key"
        )
    return str(center.center_key)


def get_identity_salt_fingerprint(salt: bytes | str | None = None) -> str:
    """Public fingerprint for verifying matching salt generations."""
    return get_identity_hash(b"endoreg-identity-salt-v1", salt=salt)


def encode_patient_identity(
    first_name: str,
    last_name: str,
    dob: date,
    center_key: str,
) -> bytes:
    """Canonical v1 JSON/UTF-8 payload; no case folding or Unicode normalization."""
    birthday = dob.date() if isinstance(dob, datetime) else dob
    return json.dumps(
        [
            "patient-identity-v1",
            first_name.strip(),
            last_name.strip(),
            birthday.isoformat(),
            center_key.strip(),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def get_patient_identity_fingerprint(
    first_name: str,
    last_name: str,
    dob: date,
    center_key: str,
    *,
    salt: bytes | str | None = None,
) -> str:
    """HMAC-SHA-256 of an unambiguously encoded tuple, not unique human identity."""
    return get_identity_hash(
        encode_patient_identity(first_name, last_name, dob, center_key), salt=salt
    )


def calculate_patient_hash(
    first_name: str,
    last_name: str,
    dob: date,
    center_name: str | None = None,
    *,
    center_key: str | None = None,
    salt: bytes | str | None = None,
) -> str:
    """Calculate the modern deterministic HMAC identity hash for a patient."""
    resolved_key = (
        center_key.strip() if center_key else get_center_key(center_name or "")
    )
    return get_patient_identity_fingerprint(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center_key=resolved_key,
        salt=salt,
    )


# Backwards compatibility alias for existing callers
get_patient_hash = calculate_patient_hash


def get_patient_examination_hash(
    first_name: str,
    last_name: str,
    dob: date,
    center_name: str | None = None,
    examination_date: date | None = None,
    *,
    center_key: str | None = None,
    salt: bytes | str | None = None,
) -> str:
    """Calculate a deterministic, collision-safe examination hash."""
    resolved_key = (
        center_key.strip() if center_key else get_center_key(center_name or "")
    )
    salt = _get_active_salt_bytes(salt)
    patient_fp = get_patient_identity_fingerprint(
        first_name=first_name,
        last_name=last_name,
        dob=dob,
        center_key=resolved_key,
        salt=salt,
    )
    exam_date_str = (
        examination_date.isoformat()
        if isinstance(examination_date, (date, datetime))
        else str(examination_date or "")
    )
    payload = json.dumps(
        ["patient-examination-v1", patient_fp, exam_date_str, resolved_key],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return get_identity_hash(payload, salt=salt)


def get_examiner_hash(
    first_name: str,
    last_name: str,
    center_name: str | None = None,
    *,
    center_key: str | None = None,
    salt: bytes | str | None = None,
) -> str:
    """Calculate the deterministic HMAC identity hash for an examiner."""
    resolved_key = (
        center_key.strip() if center_key else get_center_key(center_name or "")
    )
    payload = json.dumps(
        ["examiner-identity-v1", first_name.strip(), last_name.strip(), resolved_key],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return get_identity_hash(payload, salt=salt)


def get_hub_record_pseudonym(
    record_uuid: UUID,
    *,
    kind: HubRecordKind,
    hub_node_key: str,
    salt: bytes | str | None = None,
) -> UUID:
    """Hub-scoped UUIDv8 pseudonym; only the local identity salt relates it to the record.

    The local UUID never leaves the site. Repeated transfers of one record to one
    hub keep the same pseudonym, so follow-ups stay linkable at that hub, while
    pseudonyms for other hubs or salts are unlinkable without the salt.
    """
    hub = hub_node_key.strip()
    if not hub:
        raise ValueError("Hub record pseudonyms require the target hub node key")
    payload = json.dumps(
        ["hub-record-pseudonym-v1", kind, str(record_uuid), hub],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(_get_active_salt_bytes(salt), payload, hashlib.sha256).digest()
    value = int.from_bytes(digest[:16], "big")
    # RFC 9562 UUIDv8: version nibble 8, variant bits 10.
    value = (value & ~(0xF << 76)) | (0x8 << 76)
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    return UUID(int=value)


def get_file_hash(file: str | Path | FieldFile | None) -> str:
    """Hash local sources or stream authenticated plaintext from stored files."""
    if file is None:
        raise ValueError("HASH COULD NOT BE CREATED")
    if getattr(file, "storage", None) is not None:
        from endoreg_db.utils.storage_streaming import (
            field_file_size,
            iter_field_file_bytes,
        )

        digest = hashlib.sha256()
        size = field_file_size(file)
        if size < 0:
            raise ValueError("Stored file plaintext size must not be negative")
        consumed = 0
        if size > 0:
            for chunk in iter_field_file_bytes(
                file, start=0, end=size - 1, chunk_size=1024 * 1024
            ):
                consumed += len(chunk)
                if consumed > size:
                    raise ValueError(
                        "Stored file stream exceeds declared plaintext size"
                    )
                digest.update(chunk)
        if consumed != size or field_file_size(file) != size:
            raise ValueError(
                "Stored file plaintext size changed or stream is incomplete"
            )
        return digest.hexdigest()

    path = file if isinstance(file, (str, Path)) else getattr(file, "path", None)
    if not isinstance(path, (str, Path)):
        raise TypeError("Hash source must be a local path or stored file")
    hash_tuple = stable_file_identity(Path(path))
    if hash_tuple is not None:
        size = hash_tuple[0]
        hash_canonical = hash_tuple[2]
        logger.debug("Hash created for %s bytes", size)
        return hash_canonical
    raise ValueError("HASH COULD NOT BE CREATED")
