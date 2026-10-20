"""Fail-closed, whole-inventory conversion to the canonical UTF-8 identity HMACs.

All rows require authenticated originals, including reviewed originals after
erasure. No clinical objects are merged, created, deleted or relinked. Memory is
O(inventory); database reads and writes are batched within one transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from django.db import connection, models, transaction
from django.utils import timezone

from endoreg_db.config.identity_hashing import (
    current_identity_keyring,
    validate_identity_salt,
)
from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.examiner.examiner import Examiner
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.patient.patient_hash_alias import (
    PatientHashAlias,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.secret_rotation.identity import (
    IdentitySource,
    ReviewedExaminer,
    ReviewedIdentity,
    calculate_legacy_hash,
    calculate_modern_examination_hash as _examination_hash,
    calculate_modern_patient_hash as _patient_hash,
    identity_rotation_transaction,
    legacy_hash,
)
from endoreg_db.utils.hashs import (
    get_bytes_hash,
    get_examiner_hash,
    get_identity_salt_fingerprint,
)
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=models.Model)


@dataclass(frozen=True)
class HashDiff:
    model: str
    record_id: int
    field: str
    before: str | None
    after: str | None


@dataclass(frozen=True)
class LegacyMigrationResult:
    dry_run: bool
    patients: int
    examinations: int
    metadata: int
    examiners: int
    aliases: int
    diffs: tuple[HashDiff, ...]


def _inventory(model: type[ModelT], batch_size: int, *fields: str) -> dict[int, ModelT]:
    return {
        int(row.pk): row
        for row in model._default_manager.only(*fields)
        .order_by("pk")
        .iterator(chunk_size=batch_size)
    }


def _source(
    row: SensitiveMeta, reviewed: dict[int, ReviewedIdentity]
) -> IdentitySource:
    supplied = reviewed.get(int(row.pk))
    if supplied is not None:
        if not supplied.reviewed_by.strip() or not supplied.review_reference.strip():
            raise ValueError("Reviewed source requires reviewer and reference")
        return supplied
    if row.direct_identifiers_cleared_at is not None:
        raise ValueError("Erased metadata requires a reviewed source mapping")
    dob: object = row.patient_dob
    if isinstance(dob, datetime):
        dob = (
            timezone.localdate(dob, timezone.get_default_timezone())
            if timezone.is_aware(dob)
            else dob.date()
        )
    return IdentitySource.model_validate(
        {
            "first_name": row.patient_first_name,
            "last_name": row.patient_last_name,
            "dob": dob,
            "examination_date": row.examination_date,
        }
    )


def _target(targets: dict[int, str], pk: int, value: str) -> None:
    if pk in targets and targets[pk] != value:
        raise ValueError("One record resolves to conflicting source identities")
    targets[pk] = value


def _collisions(targets: dict[int, str], current: dict[int, str]) -> None:
    owners: dict[str, int] = {}
    for pk, value in current.items():
        if value in owners and owners[value] != pk:
            raise ValueError("Existing hashes resolve to multiple records")
        owners[value] = pk
    seen: dict[str, int] = {}
    for pk, value in targets.items():
        if (value in owners and owners[value] != pk) or (
            value in seen and seen[value] != pk
        ):
            raise ValueError("Target hash collision requires explicit identity review")
        seen[value] = pk


def _set(
    row: models.Model, field: str, value: str | None, diffs: list[HashDiff]
) -> bool:
    previous: object = getattr(row, field)
    if previous == value:
        return False
    if previous is not None and not isinstance(previous, str):
        raise ValueError("Persisted hash field has an invalid type")
    diffs.append(HashDiff(row._meta.label, int(row.pk), field, previous, value))
    setattr(row, field, value)
    return True


def _write(
    model: type[ModelT], rows: list[ModelT], fields: list[str], size: int
) -> None:
    # Slice explicitly: bulk_update otherwise builds all CASE clauses in memory.
    for offset in range(0, len(rows), size):
        model._default_manager.bulk_update(
            rows[offset : offset + size], fields, batch_size=size
        )


def _verify(
    model: type[ModelT], expected: dict[int, ModelT], fields: list[str], size: int
) -> None:
    actual = _inventory(model, size, *fields)
    if actual.keys() != expected.keys() or any(
        getattr(row, field) != getattr(expected[pk], field)
        for pk, row in actual.items()
        for field in fields
    ):
        raise ValueError("Post-flight inventory or relationship verification failed")


@identity_rotation_transaction
def migrate_legacy_hashes(
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    reviewed: dict[int, ReviewedIdentity] | None = None,
    examiners: tuple[ReviewedExaminer, ...] = (),
) -> LegacyMigrationResult:
    """Plan every target before writes; any failure aborts the whole transaction.

    Requires PostgreSQL and the configured keyring. The shared decorator acquires
    advisory lock (20260921, 2) and pins the keyring. Table locks also fence direct
    ORM writers which do not participate in that advisory-lock protocol.
    """
    if connection.vendor != "postgresql":
        raise ValueError("Strict identity migration requires PostgreSQL")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ring = current_identity_keyring()
    if ring is None:
        raise ValueError("An identity keyring must be explicitly configured")
    validate_identity_salt(ring.active)
    active = get_identity_salt_fingerprint(ring.active)
    evidence = reviewed or {}
    if any(pk != item.sensitive_meta_id for pk, item in evidence.items()):
        raise ValueError("Reviewed identity mapping keys must match row identifiers")
    examiner_sources = {item.examiner_id: item for item in examiners}
    if len(examiner_sources) != len(examiners):
        raise ValueError("Reviewed examiner identifiers must be unique")
    tables = sorted(
        model._meta.db_table
        for model in (
            Center,
            Patient,
            PatientExamination,
            SensitiveMeta,
            Examiner,
            PatientHashAlias,
        )
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "LOCK TABLE "
            + ", ".join(connection.ops.quote_name(t) for t in tables)
            + " IN SHARE ROW EXCLUSIVE MODE"
        )

    centers = _inventory(Center, batch_size, "name", "center_key")
    keys = [center.center_key for center in centers.values()]
    if any(not key or key != key.strip() for key in keys) or len(set(keys)) != len(
        keys
    ):
        raise ValueError(
            "Every center requires a populated, canonical, unique center_key"
        )
    patients = _inventory(Patient, batch_size, "center_id", "patient_hash")
    examinations = _inventory(PatientExamination, batch_size, "patient_id", "hash")
    metadata = _inventory(
        SensitiveMeta,
        batch_size,
        "center_id",
        "pseudo_patient_id",
        "pseudo_examination_id",
        "patient_first_name",
        "patient_last_name",
        "patient_dob",
        "examination_date",
        "patient_hash",
        "examination_hash",
        "identity_fingerprint",
        "identity_salt_fingerprint",
        "direct_identifiers_cleared_at",
    )
    examiner_rows = _inventory(
        Examiner,
        batch_size,
        "center_id",
        "first_name",
        "last_name",
        "hash",
        "identity_fingerprint",
        "identity_salt_fingerprint",
    )
    aliases = _inventory(
        PatientHashAlias,
        batch_size,
        "center_id",
        "patient_id",
        "patient_hash",
        "salt_fingerprint",
    )
    if (
        evidence.keys() - metadata.keys()
        or examiner_sources.keys() - examiner_rows.keys()
    ):
        raise ValueError("Reviewed mapping refers to an unknown record")

    patient_targets: dict[int, str] = {}
    examination_targets: dict[int, str] = {}
    old_generations: dict[int, str] = {}
    diffs: list[HashDiff] = []
    changed_metadata: list[SensitiveMeta] = []
    for row in metadata.values():
        center = centers.get(row.center_id) if row.center_id is not None else None
        patient = (
            patients.get(row.pseudo_patient_id)
            if row.pseudo_patient_id is not None
            else None
        )
        if center is None or patient is None or patient.center_id != center.pk:
            raise ValueError(
                "Metadata requires resolvable center and patient ownership"
            )
        _center_scope(ring, center)
        if row.patient_hash != patient.patient_hash:
            raise ValueError("Metadata and patient hashes disagree")
        source = _source(row, evidence)
        matching = [
            salt
            for salt in ring.readers
            if row.patient_hash
            in {
                calculate_legacy_hash(source, center.name, salt),
                _patient_hash(source, center.center_key, salt),
                get_bytes_hash(_patient_hash(source, center.center_key, salt).encode()),
            }
        ]
        if len(matching) != 1:
            raise ValueError(
                "Source does not uniquely authenticate the stored patient hash"
            )
        old_salt = matching[0]
        generation = get_identity_salt_fingerprint(old_salt)
        if (
            row.identity_salt_fingerprint
            and row.identity_salt_fingerprint != generation
        ):
            raise ValueError("Patient salt evidence conflicts with its stored hash")
        if row.identity_fingerprint and row.identity_fingerprint not in {
            _patient_hash(source, center.center_key, old_salt),
            _patient_hash(source, str(center.pk), old_salt),
        }:
            raise ValueError("Patient identity evidence conflicts with its source")
        target = _patient_hash(source, center.center_key, ring.active)
        _target(patient_targets, int(patient.pk), target)
        _target(old_generations, int(patient.pk), generation)
        exam_target: str | None = None
        if row.pseudo_examination_id is not None:
            exam = examinations.get(row.pseudo_examination_id)
            if (
                exam is None
                or exam.patient_id != patient.pk
                or exam.hash != row.examination_hash
            ):
                raise ValueError(
                    "Examination relationship or stored hash is inconsistent"
                )
            if not any(
                row.examination_hash
                in {
                    calculate_legacy_hash(source, center.name, salt, examination=True),
                    _examination_hash(source, center.center_key, salt),
                    get_bytes_hash(
                        _examination_hash(source, center.center_key, salt).encode()
                    ),
                }
                for salt in ring.readers
            ):
                raise ValueError(
                    "Examination hash does not authenticate its source date"
                )
            exam_target = _examination_hash(source, center.center_key, ring.active)
            _target(examination_targets, int(exam.pk), exam_target)
        elif row.examination_hash:
            raise ValueError("Examination hash has no linked examination")
        changed = False
        for field, value in (
            ("patient_hash", target),
            ("examination_hash", exam_target),
            ("identity_fingerprint", target),
            ("identity_salt_fingerprint", active),
        ):
            changed = _set(row, field, value, diffs) or changed
        if changed:
            changed_metadata.append(row)

    if (
        patient_targets.keys() != patients.keys()
        or examination_targets.keys() != examinations.keys()
    ):
        raise ValueError(
            "Every patient and examination requires metadata source evidence"
        )
    _collisions(
        patient_targets, {pk: str(row.patient_hash) for pk, row in patients.items()}
    )
    _collisions(
        examination_targets, {pk: str(row.hash) for pk, row in examinations.items()}
    )
    alias_index: dict[tuple[int, str], PatientHashAlias] = {}
    for alias in aliases.values():
        patient = patients.get(alias.patient_id)
        if patient is None or patient.center_id != alias.center_id:
            raise ValueError(
                "Historical alias has inconsistent patient or center ownership"
            )
        alias_index[alias.center_id, alias.patient_hash] = alias
    new_aliases: list[PatientHashAlias] = []
    changed_patients: list[Patient] = []
    for pk, target in patient_targets.items():
        patient = patients[pk]
        if patient.center_id is None:
            raise ValueError("Patient center is missing")
        old_hash = str(patient.patient_hash)
        for value, generation in ((old_hash, old_generations[pk]), (target, active)):
            alias = alias_index.get((patient.center_id, value))
            if alias is not None and alias.patient_id != pk:
                raise ValueError(
                    "Target or current hash conflicts with a historical alias"
                )
            if alias is not None and alias.salt_fingerprint != generation:
                raise ValueError(
                    "Historical alias generation conflicts with source evidence"
                )
        if old_hash != target:
            existing = alias_index.get((patient.center_id, old_hash))
            if existing is None:
                new_aliases.append(
                    PatientHashAlias(
                        patient_id=pk,
                        center_id=patient.center_id,
                        patient_hash=old_hash,
                        salt_fingerprint=old_generations[pk],
                    )
                )
            _set(patient, "patient_hash", target, diffs)
            changed_patients.append(patient)
    changed_examinations = [
        row
        for pk, row in examinations.items()
        if _set(row, "hash", examination_targets[pk], diffs)
    ]
    changed_examiners = _plan_examiners(
        examiner_rows, centers, examiner_sources, ring, diffs
    )
    result = LegacyMigrationResult(
        dry_run,
        len(changed_patients),
        len(changed_examinations),
        len(changed_metadata),
        len(changed_examiners),
        len(new_aliases),
        tuple(diffs),
    )
    if dry_run:
        return result

    # The entire plan, including aliases and examiners, is validated before INSERT/UPDATE.
    PatientHashAlias.objects.bulk_create(new_aliases, batch_size=batch_size)
    _write(Patient, changed_patients, ["patient_hash"], batch_size)
    _write(PatientExamination, changed_examinations, ["hash"], batch_size)
    metadata_fields = [
        "patient_hash",
        "examination_hash",
        "identity_fingerprint",
        "identity_salt_fingerprint",
    ]
    examiner_fields = ["hash", "identity_fingerprint", "identity_salt_fingerprint"]
    _write(SensitiveMeta, changed_metadata, metadata_fields, batch_size)
    _write(Examiner, changed_examiners, examiner_fields, batch_size)
    if SensitiveMeta.objects.exclude(identity_salt_fingerprint=active).exists() or (
        Examiner.objects.exclude(identity_salt_fingerprint=active).exists()
    ):
        raise ValueError("Post-flight found identities outside the active generation")
    _verify(Patient, patients, ["patient_hash", "center_id"], batch_size)
    _verify(PatientExamination, examinations, ["hash", "patient_id"], batch_size)
    _verify(
        SensitiveMeta,
        metadata,
        metadata_fields + ["center_id", "pseudo_patient_id", "pseudo_examination_id"],
        batch_size,
    )
    _verify(Examiner, examiner_rows, examiner_fields + ["center_id"], batch_size)
    for alias in new_aliases:
        aliases[int(alias.pk)] = alias
    _verify(
        PatientHashAlias,
        aliases,
        ["patient_id", "center_id", "patient_hash", "salt_fingerprint"],
        batch_size,
    )
    transaction.on_commit(
        lambda: emit_structured_event(
            logger,
            "legacy_identity_migration_committed",
            patients=result.patients,
            examinations=result.examinations,
            metadata=result.metadata,
            examiners=result.examiners,
            aliases=result.aliases,
        )
    )
    return result


def _center_scope(ring: SecretKeyring, center: Center) -> None:
    if ring.center_id is not None and ring.center_id != center.center_key:
        raise ValueError("Configured identity keyring does not own this center")


def _plan_examiners(
    rows: dict[int, Examiner],
    centers: dict[int, Center],
    reviewed: dict[int, ReviewedExaminer],
    ring: SecretKeyring,
    diffs: list[HashDiff],
) -> list[Examiner]:
    targets: dict[int, str] = {}
    changed: list[Examiner] = []
    current = {pk: row.hash for pk, row in rows.items()}
    active = get_identity_salt_fingerprint(ring.active)
    for pk, row in rows.items():
        center = centers.get(row.center_id) if row.center_id is not None else None
        if center is None:
            raise ValueError("Examiner requires a resolvable center")
        _center_scope(ring, center)
        source = reviewed.get(pk)
        if source is not None and (
            not source.reviewed_by.strip() or not source.review_reference.strip()
        ):
            raise ValueError("Reviewed examiner requires reviewer and reference")
        first: object = source.first_name if source else row.first_name
        last: object = source.last_name if source else row.last_name
        if not isinstance(first, str) or not isinstance(last, str):
            raise ValueError("Examiner requires original names")
        IdentitySource.validate_name(first)
        IdentitySource.validate_name(last)
        matching = [
            salt
            for salt in ring.readers
            if row.hash
            in {
                legacy_hash(
                    first, last, None, center.name, None, salt, double_hash=False
                ),
                get_examiner_hash(first, last, center_key=center.center_key, salt=salt),
            }
        ]
        if len(matching) != 1:
            raise ValueError(
                "Examiner requires reviewed original names matching its stored hash"
            )
        salt = matching[0]
        if (
            row.identity_salt_fingerprint
            and row.identity_salt_fingerprint != get_identity_salt_fingerprint(salt)
        ):
            raise ValueError("Examiner salt evidence conflicts with its stored hash")
        if row.identity_fingerprint and row.identity_fingerprint not in {
            get_examiner_hash(first, last, center_key=center.center_key, salt=salt),
            get_examiner_hash(first, last, center_key=str(center.pk), salt=salt),
        }:
            raise ValueError("Examiner identity evidence conflicts with its source")
        target = get_examiner_hash(
            first, last, center_key=center.center_key, salt=ring.active
        )
        targets[pk] = target
        updated = False
        for field, value in (
            ("hash", target),
            ("identity_fingerprint", target),
            ("identity_salt_fingerprint", active),
        ):
            updated = _set(row, field, value, diffs) or updated
        if updated:
            changed.append(row)
    _collisions(targets, current)
    return changed
