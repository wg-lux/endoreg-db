"""Read-only local record binding for private tuple comparison, not person proof.

Trusted callers supply authorization sets. Snapshots stay in local memory; only
opaque references may be shared. Revalidation is required at completion and at
acceptance; it is not a database lock spanning the remote computation.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID, uuid4

from django.views.decorators.debug import sensitive_variables

from endoreg_db.config.identity_hashing import current_identity_keyring
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.state.sensitive_meta import SensitiveMetaState
from endoreg_db.models.administration.person.patient.patient_hash_alias import (
    PatientHashAlias,
)
from endoreg_db.schemas.patient_matching import MatchingRecordRef
from endoreg_db.services.patient_identity.hash_aliases import resolve_patient_hash
from endoreg_db.utils.hashs import (
    encode_patient_identity,
    get_identity_salt_fingerprint,
)


class MatchingInputError(ValueError):
    """Redacted local binding or authorization failure."""


class MatchingKeyEpochUnavailable(MatchingInputError):
    """No explicitly authorized enrolled key generation; never a nonmatch."""


@dataclass(frozen=True)
class MatchingAuthorization:
    allowed_center_ids: frozenset[int]
    allowed_patient_uuids: frozenset[UUID]


@dataclass(frozen=True, repr=False)
class _Evidence:
    meta_id: int
    patient_id: int
    patient_uuid: UUID
    center_id: int
    center_key: str
    patient_hash: str
    identity_fingerprint: str
    key_epoch: str
    current_patient_hash: str
    first_name: str | None
    last_name: str | None
    birthday: date | None
    erased_at: datetime | None
    names_verified: bool
    dob_verified: bool
    state_updated_at: datetime | None


@dataclass(frozen=True)
class SourceMatchingInput:
    reference: MatchingRecordRef
    key: bytes = field(repr=False)
    target: bytes = field(repr=False)
    evidence: _Evidence = field(repr=False)


@dataclass(frozen=True)
class CandidateMatchingInput:
    reference: MatchingRecordRef
    source_center_key: str
    payload: bytes = field(repr=False)
    evidence: _Evidence = field(repr=False)


def _text(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise MatchingInputError("Invalid local identity field type")


def _digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MatchingInputError("Missing or legacy identity evidence")
    return value


@sensitive_variables()
def _read(sensitive_meta_id: int, authorization: MatchingAuthorization) -> _Evidence:
    row = (
        SensitiveMeta.objects.select_related("center", "pseudo_patient")
        .filter(
            pk=sensitive_meta_id,
            center_id__in=authorization.allowed_center_ids,
            pseudo_patient__uuid__in=authorization.allowed_patient_uuids,
        )
        .first()
    )
    if row is None or row.center is None or row.pseudo_patient is None:
        raise MatchingInputError("Record unavailable or unauthorized")
    center, patient = row.center, row.pseudo_patient
    if patient.center_id != center.pk or not center.center_key.strip():
        raise MatchingInputError("Inconsistent local center ownership")
    # Canonical identifiers must identify exactly one persisted center.
    if type(center).objects.filter(center_key=center.center_key).count() != 1:
        raise MatchingInputError("Ambiguous local center identifier")
    tag = _digest(row.patient_hash)
    identity = _digest(row.identity_fingerprint)
    epoch = _digest(row.identity_salt_fingerprint)
    if tag != identity:
        raise MatchingInputError("Unbound or legacy local fingerprint")
    resolved = resolve_patient_hash(center=center, patient_hash=tag)
    if resolved is None or resolved.pk != patient.pk:
        raise MatchingInputError("Inconsistent local patient ownership")
    if (
        patient.patient_hash != tag
        and not PatientHashAlias.objects.filter(
            patient=patient,
            center=center,
            patient_hash=tag,
            salt_fingerprint=epoch,
        ).exists()
    ):
        raise MatchingInputError("Historical record lacks generation binding")
    birthday: object = row.patient_dob
    erased: object = row.direct_identifiers_cleared_at
    if birthday is not None and not isinstance(birthday, date):
        raise MatchingInputError("Invalid local birth date")
    if erased is not None and not isinstance(erased, datetime):
        raise MatchingInputError("Invalid local erasure state")
    state = SensitiveMetaState.objects.filter(origin_id=row.pk).first()
    return _Evidence(
        row.pk,
        patient.pk,
        patient.uuid,
        center.pk,
        center.center_key,
        tag,
        identity,
        epoch,
        _digest(patient.patient_hash),
        _text(row.patient_first_name),
        _text(row.patient_last_name),
        birthday,
        erased,
        state.names_verified if state else False,
        state.dob_verified if state else False,
        state.updated_at if state else None,
    )


def _reference(evidence: _Evidence) -> MatchingRecordRef:
    return MatchingRecordRef(
        uuid=evidence.patient_uuid,
        row_version=uuid4(),
        payload_ref=uuid4(),
        centre=evidence.center_key,
        scheme="patient-identity-v1",
        key_epoch=evidence.key_epoch,
    )


@sensitive_variables()
def _key(evidence: _Evidence, allowed_key_epochs: frozenset[str]) -> bytes:
    if evidence.key_epoch not in allowed_key_epochs:
        raise MatchingKeyEpochUnavailable("Key epoch is not authorized")
    ring = current_identity_keyring()
    if ring is None or (
        ring.center_id is not None and ring.center_id != evidence.center_key
    ):
        raise MatchingKeyEpochUnavailable("Enrolled local keyring unavailable")
    keys = [
        key
        for key in ring.readers
        if hmac.compare_digest(
            get_identity_salt_fingerprint(key),
            evidence.key_epoch,
        )
    ]
    if len(keys) != 1:
        raise MatchingKeyEpochUnavailable("Enrolled key epoch unavailable or ambiguous")
    return keys[0]


@sensitive_variables()
def prepare_source_input(
    *,
    sensitive_meta_id: int,
    authorization: MatchingAuthorization,
    allowed_key_epochs: frozenset[str],
) -> SourceMatchingInput:
    evidence = _read(sensitive_meta_id, authorization)
    return SourceMatchingInput(
        _reference(evidence),
        _key(evidence, allowed_key_epochs),
        bytes.fromhex(evidence.patient_hash),
        evidence,
    )


@sensitive_variables()
def prepare_candidate_input(
    *,
    sensitive_meta_id: int,
    authorization: MatchingAuthorization,
    source_center_key: str,
) -> CandidateMatchingInput:
    evidence = _read(sensitive_meta_id, authorization)
    if (
        evidence.erased_at is not None
        or not evidence.names_verified
        or not evidence.dob_verified
        or not evidence.first_name
        or not evidence.first_name.strip()
        or not evidence.last_name
        or not evidence.last_name.strip()
        or evidence.birthday is None
    ):
        raise MatchingInputError("Reviewed original demographic tuple unavailable")
    if not source_center_key or source_center_key != source_center_key.strip():
        raise MatchingInputError("Canonical source center identifier required")
    payload = encode_patient_identity(
        evidence.first_name, evidence.last_name, evidence.birthday, source_center_key
    )
    return CandidateMatchingInput(
        _reference(evidence), source_center_key, payload, evidence
    )


@sensitive_variables()
def assert_source_unchanged(
    snapshot: SourceMatchingInput,
    *,
    authorization: MatchingAuthorization,
    allowed_key_epochs: frozenset[str],
) -> None:
    current = prepare_source_input(
        sensitive_meta_id=snapshot.evidence.meta_id,
        authorization=authorization,
        allowed_key_epochs=allowed_key_epochs,
    )
    if (
        current.evidence != snapshot.evidence
        or current.key != snapshot.key
        or current.target != snapshot.target
    ):
        raise MatchingInputError("Source record changed after preparation")


@sensitive_variables()
def assert_candidate_unchanged(
    snapshot: CandidateMatchingInput,
    *,
    authorization: MatchingAuthorization,
) -> None:
    current = prepare_candidate_input(
        sensitive_meta_id=snapshot.evidence.meta_id,
        authorization=authorization,
        source_center_key=snapshot.source_center_key,
    )
    if current.evidence != snapshot.evidence or current.payload != snapshot.payload:
        raise MatchingInputError("Candidate record changed after preparation")
