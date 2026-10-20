from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from threading import Event

import pytest
from django.core.management import call_command, CommandError
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.examiner.examiner import Examiner
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.patient.patient_hash_alias import (
    PatientHashAlias,
)
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.services.secret_rotation.identity import (
    ReviewedExaminer,
    ReviewedIdentity,
    legacy_hash,
)
from endoreg_db.services.secret_rotation.legacy_hashes import migrate_legacy_hashes
from endoreg_db.utils.hashs import get_bytes_hash, get_identity_salt_fingerprint


@dataclass(frozen=True)
class Inventory:
    ring: SecretKeyring
    center: Center
    patient: Patient
    exam: PatientExamination
    meta: SensitiveMeta
    examiner: Examiner


@pytest.fixture
def inventory(monkeypatch: pytest.MonkeyPatch) -> Inventory:
    ring = SecretKeyring(
        active=secrets.token_hex(32).encode(), retiring=(b"default_salt",)
    )
    monkeypatch.setattr(
        "endoreg_db.config.identity_hashing.configured_identity_keyring", lambda: ring
    )
    center = Center.objects.create(
        name="Migration source", center_key="migration-source"
    )
    patient_hash = legacy_hash(
        "Ada", "Müller", date(1980, 1, 2), center.name, None, ring.retiring[0]
    )
    exam_hash = legacy_hash(
        "Ada",
        "Müller",
        date(1980, 1, 2),
        center.name,
        date(2026, 9, 21),
        ring.retiring[0],
    )
    patient = Patient.objects.create(
        center=center, patient_hash=patient_hash, is_real_person=False
    )
    exam = PatientExamination.objects.create(patient=patient, hash=exam_hash)
    meta = SensitiveMeta(
        center=center,
        pseudo_patient=patient,
        pseudo_examination=exam,
        patient_first_name="Ada",
        patient_last_name="Müller",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
        patient_hash=patient_hash,
        examination_hash=exam_hash,
    )
    # Construct historical rows without executing modern ingestion/save hooks.
    SensitiveMeta.objects.bulk_create([meta])
    examiner = Examiner.objects.create(
        center=center,
        first_name="René",
        last_name="Müller",
        hash=legacy_hash(
            "René",
            "Müller",
            None,
            center.name,
            None,
            ring.retiring[0],
            double_hash=False,
        ),
    )
    return Inventory(ring, center, patient, exam, meta, examiner)


def _snapshot() -> tuple[list[tuple[object, ...]], ...]:
    return tuple(
        list(model._default_manager.order_by("pk").values_list())
        for model in (
            Patient,
            PatientExamination,
            SensitiveMeta,
            Examiner,
            PatientHashAlias,
        )
    )


def _digest(salt: bytes, parts: list[str]) -> str:
    return hmac.new(
        salt,
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def test_full_migration_unicode_aliases_and_idempotency(inventory: Inventory) -> None:
    result = migrate_legacy_hashes(batch_size=1)
    assert (
        result.patients,
        result.examinations,
        result.metadata,
        result.examiners,
        result.aliases,
    ) == (1, 1, 1, 1, 1)
    patient_hash = _digest(
        inventory.ring.active,
        [
            "patient-identity-v1",
            "Ada",
            "Müller",
            "1980-01-02",
            inventory.center.center_key,
        ],
    )
    exam_hash = _digest(
        inventory.ring.active,
        [
            "patient-examination-v1",
            patient_hash,
            "2026-09-21",
            inventory.center.center_key,
        ],
    )
    examiner_hash = _digest(
        inventory.ring.active,
        ["examiner-identity-v1", "René", "Müller", inventory.center.center_key],
    )
    inventory.meta.refresh_from_db()
    inventory.exam.refresh_from_db()
    inventory.examiner.refresh_from_db()
    assert (
        inventory.meta.patient_hash
        == inventory.meta.identity_fingerprint
        == patient_hash
    )
    assert inventory.meta.examination_hash == inventory.exam.hash == exam_hash
    assert (
        inventory.examiner.hash
        == inventory.examiner.identity_fingerprint
        == examiner_hash
    )
    assert inventory.meta.identity_salt_fingerprint == get_identity_salt_fingerprint(
        inventory.ring.active
    )
    assert inventory.meta.pseudo_patient_id == inventory.patient.pk
    assert inventory.meta.pseudo_examination_id == inventory.exam.pk
    assert inventory.exam.patient_id == inventory.patient.pk
    alias = PatientHashAlias.objects.get(patient=inventory.patient)
    assert alias.patient_hash == inventory.patient.patient_hash
    assert alias.center_id == inventory.center.pk
    before = _snapshot()
    with CaptureQueriesContext(connection) as queries:
        repeat = migrate_legacy_hashes(batch_size=1)
    assert repeat.diffs == () and repeat.aliases == 0
    assert _snapshot() == before
    assert not any(q["sql"].lstrip().startswith(("UPDATE", "INSERT")) for q in queries)


def test_dry_run_command_has_diffs_and_no_writes(inventory: Inventory) -> None:
    before = _snapshot()
    output = StringIO()
    with CaptureQueriesContext(connection) as queries:
        call_command(
            "migrate_legacy_hashes", "--dry-run", "--batch-size", "1", stdout=output
        )
    assert '"dry_run": true' in output.getvalue()
    assert '"before"' in output.getvalue() and '"after"' in output.getvalue()
    assert "Müller" not in output.getvalue()
    assert _snapshot() == before
    assert not any(q["sql"].lstrip().startswith(("UPDATE", "INSERT")) for q in queries)


def test_preflight_batches_do_not_fetch_each_row_again(inventory: Inventory) -> None:
    with CaptureQueriesContext(connection) as single:
        migrate_legacy_hashes(dry_run=True, batch_size=2)
    SensitiveMeta.objects.bulk_create(
        [
            SensitiveMeta(
                center=inventory.center,
                pseudo_patient=inventory.patient,
                pseudo_examination=inventory.exam,
                patient_first_name="Ada",
                patient_last_name="Müller",
                patient_dob=inventory.meta.patient_dob,
                examination_date=inventory.meta.examination_date,
                patient_hash=inventory.meta.patient_hash,
                examination_hash=inventory.meta.examination_hash,
                text="Unneeded report body",
            )
            for _ in range(12)
        ]
    )
    with CaptureQueriesContext(connection) as multiple:
        assert migrate_legacy_hashes(dry_run=True, batch_size=2).metadata == 13
    assert len(multiple) == len(single)
    assert all(
        '"endoreg_db_sensitivemeta"."text"' not in item["sql"] for item in multiple
    )


@pytest.mark.parametrize("erased", [False, True])
def test_returning_import_after_migration_preserves_clinical_links(
    inventory: Inventory, erased: bool
) -> None:
    migrate_legacy_hashes()
    if erased:
        SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(
            patient_first_name=None,
            patient_last_name=None,
            patient_dob=None,
            direct_identifiers_cleared_at=timezone.now(),
        )
    gender = Gender.objects.create(name="female")
    repeated = SensitiveMeta.objects.create(
        center=inventory.center,
        patient_first_name="Ada",
        patient_last_name="Müller",
        patient_gender=gender,
        patient_dob=inventory.meta.patient_dob,
        examination_date=inventory.meta.examination_date,
    )
    assert repeated.pseudo_patient_id == inventory.patient.pk
    assert repeated.pseudo_examination_id == inventory.exam.pk
    assert Patient.objects.count() == PatientExamination.objects.count() == 1
    inventory.meta.refresh_from_db()
    assert repeated.patient_hash == inventory.meta.patient_hash
    assert repeated.examination_hash == inventory.meta.examination_hash
    if erased:
        assert inventory.meta.patient_first_name is None


def test_intermediate_double_hmac_encoding_is_upgraded_from_originals(
    inventory: Inventory,
) -> None:
    migrate_legacy_hashes()
    inventory.meta.refresh_from_db()
    patient_hash = get_bytes_hash(str(inventory.meta.patient_hash).encode())
    exam_hash = get_bytes_hash(str(inventory.meta.examination_hash).encode())
    Patient.objects.filter(pk=inventory.patient.pk).update(patient_hash=patient_hash)
    PatientExamination.objects.filter(pk=inventory.exam.pk).update(hash=exam_hash)
    SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(
        patient_hash=patient_hash, examination_hash=exam_hash
    )
    result = migrate_legacy_hashes()
    assert result.patients == result.examinations == result.aliases == 1
    assert migrate_legacy_hashes().diffs == ()


@pytest.mark.parametrize(
    "failure",
    [
        "center_blank",
        "center_whitespace",
        "source",
        "date",
        "link",
        "exam_source",
        "examiner_source",
        "salt",
        "orphan",
        "stamp",
    ],
)
def test_preflight_rejects_invalid_inventory_before_writes(
    inventory: Inventory,
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if failure == "center_blank":
        Center.objects.filter(pk=inventory.center.pk).update(center_key="")
    elif failure == "center_whitespace":
        Center.objects.filter(pk=inventory.center.pk).update(
            center_key=" migration-source "
        )
    elif failure == "source":
        SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(
            patient_first_name=None
        )
    elif failure == "date":
        SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(patient_dob=None)
    elif failure == "link":
        SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(
            pseudo_examination=None
        )
    elif failure == "exam_source":
        SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(examination_date=None)
    elif failure == "examiner_source":
        Examiner.objects.filter(pk=inventory.examiner.pk).update(
            first_name="Substitute"
        )
    elif failure == "salt":
        monkeypatch.setattr(
            "endoreg_db.config.identity_hashing.configured_identity_keyring",
            lambda: SecretKeyring(active=b"weak"),
        )
    elif failure == "orphan":
        PatientExamination.objects.create(patient=inventory.patient, hash="unmapped")
    else:
        SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(
            identity_salt_fingerprint="invalid"
        )
    before = _snapshot()
    output = StringIO()
    with CaptureQueriesContext(connection) as queries, pytest.raises(CommandError):
        call_command("migrate_legacy_hashes", stdout=output)
    assert _snapshot() == before
    assert not any(q["sql"].lstrip().startswith(("UPDATE", "INSERT")) for q in queries)


def test_ambiguous_legacy_concatenation_is_detected_before_writes(
    inventory: Inventory,
) -> None:
    # Both original tuples concatenate to the same legacy input.
    duplicate = SensitiveMeta(
        center=inventory.center,
        pseudo_patient=inventory.patient,
        pseudo_examination=inventory.exam,
        patient_first_name="Ad",
        patient_last_name="aMüller",
        patient_dob=inventory.meta.patient_dob,
        examination_date=inventory.meta.examination_date,
        patient_hash=inventory.meta.patient_hash,
        examination_hash=inventory.meta.examination_hash,
    )
    SensitiveMeta.objects.bulk_create([duplicate])
    before = _snapshot()
    with (
        CaptureQueriesContext(connection) as queries,
        pytest.raises(ValueError, match="conflicting source"),
    ):
        migrate_legacy_hashes()
    assert _snapshot() == before
    assert not any(q["sql"].lstrip().startswith(("UPDATE", "INSERT")) for q in queries)


def test_intra_batch_target_collision(inventory: Inventory) -> None:
    # Trimming is new canonical behavior: different old hashes must not merge.
    old_hash = legacy_hash(
        " Ada",
        "Müller",
        date(1980, 1, 2),
        inventory.center.name,
        None,
        inventory.ring.retiring[0],
    )
    patient = Patient.objects.create(center=inventory.center, patient_hash=old_hash)
    SensitiveMeta.objects.bulk_create(
        [
            SensitiveMeta(
                center=inventory.center,
                pseudo_patient=patient,
                patient_first_name=" Ada",
                patient_last_name="Müller",
                patient_dob=inventory.meta.patient_dob,
                patient_hash=old_hash,
            )
        ]
    )
    before = _snapshot()
    with pytest.raises(ValueError, match="collision"):
        migrate_legacy_hashes()
    assert _snapshot() == before


def test_reviewed_erased_source_and_safe_rerun(inventory: Inventory) -> None:
    SensitiveMeta.objects.filter(pk=inventory.meta.pk).update(
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        direct_identifiers_cleared_at=timezone.now(),
    )
    review = ReviewedIdentity(
        sensitive_meta_id=inventory.meta.pk,
        first_name="Ada",
        last_name="Müller",
        dob=date(1980, 1, 2),
        examination_date=date(2026, 9, 21),
        reviewed_by="migration-reviewer",
        review_reference="review-1",
    )
    assert (
        migrate_legacy_hashes(reviewed={int(inventory.meta.pk): review}).metadata == 1
    )
    assert migrate_legacy_hashes(reviewed={int(inventory.meta.pk): review}).diffs == ()
    inventory.meta.refresh_from_db()
    assert (
        inventory.meta.patient_first_name is None and inventory.meta.patient_dob is None
    )


def test_reviewed_substituted_examiner_names(inventory: Inventory) -> None:
    Examiner.objects.filter(pk=inventory.examiner.pk).update(first_name="Substitute")
    source = ReviewedExaminer(
        examiner_id=inventory.examiner.pk,
        first_name="René",
        last_name="Müller",
        reviewed_by="reviewer",
        review_reference="examiner-source-1",
    )
    assert migrate_legacy_hashes(examiners=(source,)).examiners == 1
    assert migrate_legacy_hashes(examiners=(source,)).diffs == ()
    inventory.examiner.refresh_from_db()
    assert inventory.examiner.first_name == "Substitute"


def test_retained_alias_generation_conflict_rejects_entire_plan(
    inventory: Inventory,
) -> None:
    PatientHashAlias.objects.create(
        patient=inventory.patient,
        center=inventory.center,
        patient_hash=inventory.patient.patient_hash,
        salt_fingerprint=get_identity_salt_fingerprint(inventory.ring.active),
    )
    before = _snapshot()
    with pytest.raises(ValueError, match="alias generation"):
        migrate_legacy_hashes()
    assert _snapshot() == before


def test_mismatched_center_keyring_aborts(
    inventory: Inventory, monkeypatch: pytest.MonkeyPatch
) -> None:
    ring = SecretKeyring(
        active=inventory.ring.active,
        retiring=inventory.ring.retiring,
        center_id="another-center",
        generation=2,
    )
    monkeypatch.setattr(
        "endoreg_db.config.identity_hashing.configured_identity_keyring", lambda: ring
    )
    before = _snapshot()
    with pytest.raises(ValueError, match="does not own"):
        migrate_legacy_hashes()
    assert _snapshot() == before


@pytest.mark.parametrize("suppress_update", [False, True])
def test_late_failure_or_failed_postflight_rolls_back_every_table(
    inventory: Inventory,
    suppress_update: bool,
) -> None:
    before = _snapshot()

    def interrupt(
        execute: Callable[..., object],
        sql: str,
        params: object,
        many: bool,
        context: dict[str, object],
    ) -> object:
        if sql.startswith('UPDATE "endoreg_db_examiner"'):
            if suppress_update:
                return None
            raise ValueError("Injected final-table write failure")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(interrupt), pytest.raises(ValueError):
        migrate_legacy_hashes(batch_size=1)
    assert _snapshot() == before


@pytest.mark.django_db(transaction=True)
def test_advisory_lock_blocks_migration_until_writer_commits(
    inventory: Inventory,
) -> None:
    started = Event()

    def run() -> bool:
        try:
            started.set()
            return migrate_legacy_hashes(dry_run=True).dry_run
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(20260921, 2)")
            future = executor.submit(run)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.25)
        assert future.result(timeout=10)


@pytest.mark.django_db(transaction=True)
def test_table_lock_fences_nonparticipating_writer(inventory: Inventory) -> None:
    locked, release, writer_started = Event(), Event(), Event()

    def migrate() -> int:
        def pause(
            execute: Callable[..., object],
            sql: str,
            params: object,
            many: bool,
            context: dict[str, object],
        ) -> object:
            if sql.startswith('UPDATE "endoreg_db_patient"'):
                locked.set()
                if not release.wait(timeout=10):
                    raise RuntimeError("Test did not release migration")
            return execute(sql, params, many, context)

        try:
            with connection.execute_wrapper(pause):
                return migrate_legacy_hashes().patients
        finally:
            connections.close_all()

    def write() -> int:
        try:
            writer_started.set()
            return Patient.objects.filter(pk=inventory.patient.pk).update(
                first_name="Later update"
            )
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        migration = executor.submit(migrate)
        try:
            assert locked.wait(timeout=10)
            writer = executor.submit(write)
            assert writer_started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                writer.result(timeout=0.25)
        finally:
            release.set()
        assert migration.result(timeout=10) == 1
        assert writer.result(timeout=10) == 1
