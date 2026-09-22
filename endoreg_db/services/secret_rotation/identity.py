"""Transactional salt migration of existing identities, including legacy enrollment."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import date, datetime
from functools import wraps
from typing import TYPE_CHECKING, Callable, ParamSpec, TypeVar

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field, field_validator

from endoreg_db.config.identity_hashing import (
    current_identity_keyring,
    identity_keyring_snapshot,
)
from endoreg_db.utils.structured_logging import emit_structured_event, hash_identifier

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta

P = ParamSpec("P")
R = TypeVar("R")


class IdentitySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    dob: date
    examination_date: date | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value.strip().casefold() in {"", "unknown", "none", "null", "n/a", "-"}:
            raise ValueError("Identity source requires a non-placeholder name")
        return value

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, value: date) -> date:
        if value == date(1900, 1, 1):
            raise ValueError("Identity source requires a non-placeholder birth date")
        return value


class ReviewedIdentity(IdentitySource):
    sensitive_meta_id: int = Field(gt=0)
    reviewed_by: str = Field(min_length=1)
    review_reference: str = Field(min_length=1)


def identity_rotation_transaction(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with transaction.atomic():
            # Serialize salt-aware resolution and rotation across worker processes.
            # SQLite's write transaction is sufficient for isolated unit tests only.
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s, %s)", [20260921, 2]
                    )
            ring = current_identity_keyring()
            token = identity_keyring_snapshot.set(ring)
            try:
                return function(*args, **kwargs)
            finally:
                identity_keyring_snapshot.reset(token)

    return wrapped


def salt_fingerprint(salt: bytes) -> str:
    return hmac.new(salt, b"endoreg-identity-salt-v1", hashlib.sha256).hexdigest()


def identity_fingerprint(source: IdentitySource, center_id: int, salt: bytes) -> str:
    payload = json.dumps(
        [
            "patient-identity-v1",
            source.first_name,
            source.last_name,
            source.dob.isoformat(),
            center_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hmac.new(salt, payload, hashlib.sha256).hexdigest()


def legacy_hash(
    first: str,
    last: str,
    dob: date | None,
    center: str,
    examination: date | None,
    salt: bytes,
    *,
    double_hash: bool = True,
) -> str:
    """Exact historical encoding, restricted to migration/compatibility resolution.

    Includes the historical repeated birth date and SensitiveMeta's second hash.
    Normal identity creation still rejects default_salt.
    """
    birthday = dob.strftime("%Y-%m-%d") if dob and dob != date(1900, 1, 1) else ""
    exam = examination.strftime("%Y-%m-%d") if examination else ""
    digest = hashlib.sha256(
        f"{first}{last}{birthday}{center}{birthday}{exam}".encode() + salt
    ).hexdigest()
    return hashlib.sha256(digest.encode()).hexdigest() if double_hash else digest


def _source(
    row: SensitiveMeta, reviewed: dict[int, ReviewedIdentity]
) -> IdentitySource:
    pk: object = row.pk
    supplied = reviewed.get(pk) if isinstance(pk, int) else None
    if supplied is not None:
        return supplied
    if row.direct_identifiers_cleared_at is not None:
        raise ValueError("Erased identity requires a reviewed source mapping")
    first: object = row.patient_first_name
    last: object = row.patient_last_name
    dob: object = row.patient_dob
    exam: object = row.examination_date
    if isinstance(dob, datetime):
        dob = (
            timezone.localdate(dob, timezone.get_default_timezone())
            if timezone.is_aware(dob)
            else dob.date()
        )
    if (
        not isinstance(first, str)
        or not isinstance(last, str)
        or not isinstance(dob, date)
    ):
        raise ValueError("Incomplete source identity requires review")
    if (
        first.strip().casefold() in {"", "unknown", "none", "null", "n/a", "-"}
        or last.strip().casefold() in {"", "unknown", "none", "null", "n/a", "-"}
        or dob == date(1900, 1, 1)
    ):
        raise ValueError("Placeholder identity cannot be migrated")
    if exam is not None and not isinstance(exam, date):
        raise ValueError("Invalid examination date")
    return IdentitySource(
        first_name=first,
        last_name=last,
        dob=dob,
        examination_date=exam,
    )


def _hash(
    source: IdentitySource, center: str, salt: bytes, *, examination: bool = False
) -> str:
    return legacy_hash(
        source.first_name,
        source.last_name,
        source.dob,
        center,
        source.examination_date if examination else None,
        salt,
    )


def legacy_source_matches_ring(instance: SensitiveMeta) -> bool:
    ring = current_identity_keyring()
    if ring is None or instance.center is None:
        return False
    source = _source(instance, {})
    return any(
        _hash(source, instance.center.name, salt) == instance.patient_hash
        for salt in ring.readers
    )


@identity_rotation_transaction
def migrate_identity_group(
    instance: SensitiveMeta,
    *,
    reviewed: dict[int, ReviewedIdentity] | None = None,
    apply: bool = True,
) -> int:
    """Migrate the whole linked patient group or reject it before any writes."""
    from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
    from endoreg_db.models.administration.person.patient.patient import Patient
    from endoreg_db.models.medical.patient.patient_examination import PatientExamination

    ring = current_identity_keyring()
    if ring is None or not ring.retiring or instance.external_id is not None:
        return 0
    if instance.identity_salt_fingerprint == salt_fingerprint(ring.active):
        return 0
    evidence = reviewed or {}
    source = _source(instance, evidence)
    center = instance.center
    if center is None:
        raise ValueError("Identity rotation requires a persisted center")
    candidates = {_hash(source, center.name, salt): salt for salt in ring.readers}
    matches = list(
        Patient.objects.select_for_update().filter(patient_hash__in=candidates)
    )
    if len(matches) > 1:
        raise ValueError(
            "Salt generations resolve to different patients; explicit collision review required"
        )
    if not matches:
        return 0
    patient = matches[0]
    if patient.center_id != center.pk:
        raise ValueError("Salt rotation cannot cross center ownership")
    old_hash = str(patient.patient_hash)
    old_salt = candidates[old_hash]
    if old_salt == ring.active:
        return 0
    rows = list(
        SensitiveMeta.objects.select_for_update(of=("self",))
        .filter(Q(patient_hash=old_hash) | Q(pseudo_patient_id=patient.pk))
        .select_related("center")
    )
    if not rows:
        raise ValueError("Patient identity has no source metadata for migration")
    updates: list[tuple[SensitiveMeta, IdentitySource, str | None]] = []
    examinations: dict[int, str] = {}
    new_hash = _hash(source, center.name, ring.active)
    for row in rows:
        item = _source(row, evidence)
        if (
            row.center_id != center.pk
            or row.patient_hash != old_hash
            or row.pseudo_patient_id != patient.pk
            or row.external_id is not None
        ):
            raise ValueError("Identity links or ownership are inconsistent")
        if (
            _hash(item, center.name, old_salt) != old_hash
            or _hash(item, center.name, ring.active) != new_hash
            or identity_fingerprint(item, int(center.pk), ring.active)
            != identity_fingerprint(source, int(center.pk), ring.active)
        ):
            raise ValueError("Legacy identity collision or incompatible source mapping")
        if (
            row.identity_salt_fingerprint
            and row.identity_salt_fingerprint != salt_fingerprint(old_salt)
        ) or (
            row.identity_fingerprint
            and row.identity_fingerprint
            != identity_fingerprint(item, int(center.pk), old_salt)
        ):
            raise ValueError(
                "Persisted identity evidence does not authenticate the source mapping"
            )
        new_exam: str | None = None
        if row.examination_hash:
            if (
                item.examination_date is None
                or _hash(item, center.name, old_salt, examination=True)
                != row.examination_hash
                or row.pseudo_examination_id is None
            ):
                raise ValueError(
                    "Examination identity requires complete compatible source evidence"
                )
            new_exam = _hash(item, center.name, ring.active, examination=True)
            exam_id = int(row.pseudo_examination_id)
            if exam_id in examinations and examinations[exam_id] != new_exam:
                raise ValueError("Conflicting examination mappings")
            examinations[exam_id] = new_exam
        updates.append((row, item, new_exam))
    linked_exams = list(
        PatientExamination.objects.select_related(None)
        .select_for_update(of=("self",))
        .filter(patient_id=patient.pk)
    )
    if {int(exam.pk) for exam in linked_exams} != set(examinations):
        raise ValueError("Every linked examination requires migration source evidence")
    for exam in linked_exams:
        if not any(
            row.pseudo_examination_id == exam.pk and row.examination_hash == exam.hash
            for row, _, _ in updates
        ):
            raise ValueError("Examination link does not match its persisted identity")
        if (
            PatientExamination.objects.filter(hash=examinations[int(exam.pk)])
            .exclude(pk=exam.pk)
            .exists()
        ):
            raise ValueError("New examination hash conflicts with an existing identity")
    if apply:
        Patient.objects.filter(pk=patient.pk, patient_hash=old_hash).update(
            patient_hash=new_hash
        )
        for exam in linked_exams:
            PatientExamination.objects.filter(pk=exam.pk).update(
                hash=examinations[int(exam.pk)]
            )
        for row, item, new_exam in updates:
            SensitiveMeta.objects.filter(pk=row.pk).update(
                patient_hash=new_hash,
                examination_hash=new_exam,
                identity_fingerprint=identity_fingerprint(
                    item, int(center.pk), ring.active
                ),
                identity_salt_fingerprint=salt_fingerprint(ring.active),
            )
        # Do not reintroduce erased direct identifiers or rewrite audit history.
        if instance.pk and any(row.pk == instance.pk for row, _, _ in updates):
            instance.patient_hash = new_hash
            instance.identity_salt_fingerprint = salt_fingerprint(ring.active)
            instance.identity_fingerprint = identity_fingerprint(
                source, int(center.pk), ring.active
            )
            instance.examination_hash = (
                _hash(source, center.name, ring.active, examination=True)
                if instance.examination_hash
                else None
            )
        reviewed_refs = [
            hash_identifier(item.review_reference)
            for item in evidence.values()
            if any(row.pk == item.sensitive_meta_id for row, _, _ in updates)
        ]
        changed_rows = len(updates)
        transaction.on_commit(
            lambda: emit_structured_event(
                logger,
                "identity_salt_rotation_committed",
                metadata_rows=changed_rows,
                review_references=[*reviewed_refs],
            )
        )
    return len(updates)


@identity_rotation_transaction
def rotate_examiner_match(
    *,
    first_name: str,
    last_name: str,
    center_name: str,
    center_id: int,
    active_hash: str,
) -> None:
    """Keep examiner primary keys stable when an original name is observed again."""
    from endoreg_db.models.administration.person.examiner.examiner import Examiner

    ring = current_identity_keyring()
    if ring is None:
        return
    hashes = [
        legacy_hash(
            first_name, last_name, None, center_name, None, salt, double_hash=False
        )
        for salt in ring.readers
    ]
    matches = list(Examiner.objects.select_for_update().filter(hash__in=hashes))
    if len(matches) > 1 or any(row.center_id != center_id for row in matches):
        raise ValueError("Examiner salt rotation requires collision/ownership review")
    if matches:
        row = matches[0]
        old_salt = ring.readers[hashes.index(row.hash)]
        if (
            row.identity_salt_fingerprint
            and row.identity_salt_fingerprint != salt_fingerprint(old_salt)
        ):
            raise ValueError(
                "Examiner salt generation conflicts with persisted evidence"
            )
        expected = _examiner_fingerprint(first_name, last_name, center_id, old_salt)
        if row.identity_fingerprint and row.identity_fingerprint != expected:
            raise ValueError("Ambiguous examiner identity requires explicit review")
        Examiner.objects.filter(pk=row.pk).update(
            hash=active_hash,
            **examiner_identity_fields(first_name, last_name, center_id),
        )


def _examiner_fingerprint(first: str, last: str, center_id: int, salt: bytes) -> str:
    payload = json.dumps(
        ["examiner-identity-v1", first, last, center_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hmac.new(salt, payload, hashlib.sha256).hexdigest()


def examiner_identity_fields(first: str, last: str, center_id: int) -> dict[str, str]:
    from endoreg_db.utils.hashs import get_identity_salt

    salt = get_identity_salt().encode()
    return {
        "identity_salt_fingerprint": salt_fingerprint(salt),
        "identity_fingerprint": _examiner_fingerprint(first, last, center_id, salt),
    }


class ReviewedExaminer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    examiner_id: int = Field(gt=0)
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)
    review_reference: str = Field(min_length=1)


@identity_rotation_transaction
def migrate_reviewed_examiner(source: ReviewedExaminer, *, apply: bool) -> None:
    from endoreg_db.models.administration.person.examiner.examiner import Examiner

    ring = current_identity_keyring()
    if ring is None:
        raise ValueError("Examiner migration requires an identity keyring")
    row = (
        Examiner.objects.select_for_update(of=("self",))
        .select_related("center")
        .get(pk=source.examiner_id)
    )
    if row.center is None:
        raise ValueError("Examiner migration requires center ownership")
    hashes = [
        legacy_hash(
            source.first_name,
            source.last_name,
            None,
            row.center.name,
            None,
            salt,
            double_hash=False,
        )
        for salt in ring.readers
    ]
    if row.hash not in hashes:
        raise ValueError("Reviewed examiner source does not match the existing hash")
    if (
        row.identity_salt_fingerprint
        and row.identity_salt_fingerprint
        != salt_fingerprint(ring.readers[hashes.index(row.hash)])
    ):
        raise ValueError(
            "Reviewed examiner salt generation conflicts with persisted evidence"
        )
    if row.identity_fingerprint and row.identity_fingerprint != _examiner_fingerprint(
        source.first_name,
        source.last_name,
        int(row.center.pk),
        ring.readers[hashes.index(row.hash)],
    ):
        raise ValueError("Reviewed examiner source conflicts with persisted evidence")
    if Examiner.objects.filter(hash=hashes[0]).exclude(pk=row.pk).exists():
        raise ValueError("New examiner hash conflicts with an existing identity")
    if apply:
        Examiner.objects.filter(pk=row.pk).update(
            hash=hashes[0],
            **examiner_identity_fields(
                source.first_name, source.last_name, int(row.center.pk)
            ),
        )
