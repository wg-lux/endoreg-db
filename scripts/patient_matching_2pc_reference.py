"""Synthetic Approach A oracle; this is NOT private computation or production code.

One process sees both inputs. No input authentication, signatures, replay defense,
or clinical identity assurance is implemented. Epoch fingerprints check consistency
only: their authenticity must be established separately. Never pass real records
or keys to this evaluation. No configuration is read and no records are written.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class Candidate:
    patient_uuid: UUID
    first_name: str
    last_name: str
    birthday: date


@dataclass(frozen=True)
class Source:
    patient_uuid: UUID
    fingerprint: str
    center_key: str
    key_epoch: str


@dataclass(frozen=True)
class Result:
    outcome: Literal[
        "no_match", "single_match", "ambiguous_matches",
        "aborted_or_incomplete", "key_epoch_unavailable",
    ]
    source_uuid: UUID
    candidate_uuids: tuple[UUID, ...] = ()


def encode_patient(candidate: Candidate, source_center_key: str) -> bytes:
    """Mirror production v1 exactly, without case folding or Unicode normalization."""
    birthday = candidate.birthday
    birthday = birthday.date() if isinstance(birthday, datetime) else birthday
    return json.dumps(
        ["patient-identity-v1", candidate.first_name.strip(),
         candidate.last_name.strip(), birthday.isoformat(), source_center_key.strip()],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")


def key_bytes(key: str | bytes) -> bytes:
    """Configured hex-looking strings remain UTF-8 text, never decoded hex."""
    return key.encode("utf-8") if isinstance(key, str) else key


def fingerprint(candidate: Candidate, center_key: str, key: str | bytes) -> str:
    return hmac.digest(key_bytes(key), encode_patient(candidate, center_key), "sha256").hex()


def key_epoch(key: str | bytes) -> str:
    return hmac.digest(key_bytes(key), b"endoreg-identity-salt-v1", "sha256").hex()


def evaluate(
    source: Source,
    candidates: Sequence[Candidate],
    enrolled_keys: Mapping[str, bytes],
    *,
    candidate_set_complete: bool,
) -> Result:
    """Reference only: supplied UUID bindings and completion flag are NOT verified.

    Retained keys must be explicitly supplied by epoch. A missing key is distinct
    from a key stored under the wrong epoch. Incomplete searches disclose no partial
    relationships. A completed nonmatch applies only to this supplied candidate set.
    """
    for digest in (source.fingerprint, source.key_epoch):
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Expected a canonical SHA-256 hex digest")
    if len({c.patient_uuid for c in candidates}) != len(candidates):
        raise ValueError("Candidate UUIDs must be unique within a batch")
    key = enrolled_keys.get(source.key_epoch)
    if key is None:
        return Result("key_epoch_unavailable", source.patient_uuid)
    if not hmac.compare_digest(key_epoch(key), source.key_epoch):
        return Result("aborted_or_incomplete", source.patient_uuid)
    if not candidate_set_complete:
        return Result("aborted_or_incomplete", source.patient_uuid)
    matches = tuple(
        c.patient_uuid for c in candidates
        if hmac.compare_digest(fingerprint(c, source.center_key, key), source.fingerprint)
    )
    if not matches:
        return Result("no_match", source.patient_uuid)
    if len(matches) == 1:
        return Result("single_match", source.patient_uuid, matches)
    return Result("ambiguous_matches", source.patient_uuid, matches)
