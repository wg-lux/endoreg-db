from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from io import StringIO

import pytest
from django.test import override_settings
from django.utils import timezone
from django.db import connection, connections
from django.core.management import call_command, CommandError

from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.services.secret_rotation.identity import (
    ReviewedIdentity,
    legacy_hash,
    migrate_identity_group,
    salt_fingerprint,
)
from tests.services.test_secret_rotation import write_ring


def make_identity() -> SensitiveMeta:
    from tests.helpers.data_loader import load_gender_data

    load_gender_data()
    center = Center.objects.create(name="salt-rotation-center")
    return SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )


@pytest.mark.django_db
def test_online_import_rotates_existing_group_without_duplicate_links(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old-test-identity-salt"
    new = b"new-test-identity-salt"
    with override_settings(DJANGO_SALT=old.decode()):
        original = make_identity()
    patient_id, exam_id = original.pseudo_patient_id, original.pseudo_examination_id
    before = Patient.objects.count(), PatientExamination.objects.count()
    old_hash = original.patient_hash
    ring = write_ring(tmp_path, new, (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    repeated = SensitiveMeta.objects.create(
        center=original.center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
        examination_date=date(2026, 9, 21),
    )
    original.refresh_from_db()
    assert repeated.pseudo_patient_id == patient_id
    assert repeated.pseudo_examination_id == exam_id
    assert original.patient_hash == repeated.patient_hash != old_hash
    assert (Patient.objects.count(), PatientExamination.objects.count()) == before
    assert original.identity_salt_fingerprint == salt_fingerprint(new)
    write_ring(tmp_path, new, (), kind="identity")
    repeated.save()
    assert repeated.pseudo_patient_id == patient_id


@pytest.mark.django_db
def test_legacy_default_salt_migration_and_erased_review(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = make_identity()
    assert original.center is not None
    patient_id, exam_id = original.pseudo_patient_id, original.pseudo_examination_id
    patient_hash = legacy_hash(
        "Ada", "Lovelace", date(1980, 1, 2), original.center.name, None, b"default_salt"
    )
    exam_hash = legacy_hash(
        "Ada",
        "Lovelace",
        date(1980, 1, 2),
        original.center.name,
        date(2026, 9, 21),
        b"default_salt",
    )
    Patient.objects.filter(pk=patient_id).update(patient_hash=patient_hash)
    PatientExamination.objects.filter(pk=exam_id).update(hash=exam_hash)
    SensitiveMeta.objects.filter(pk=original.pk).update(
        patient_hash=patient_hash,
        examination_hash=exam_hash,
        identity_fingerprint="",
        identity_salt_fingerprint="",
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        direct_identifiers_cleared_at=timezone.now(),
    )
    original.refresh_from_db()
    ring = write_ring(
        tmp_path,
        b"new-test-salt",
        (b"default_salt",),
        kind="identity",
        allow_legacy=True,
    )
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    with pytest.raises(ValueError, match="reviewed"):
        migrate_identity_group(original)
    original.refresh_from_db()
    assert original.patient_hash == patient_hash
    review = ReviewedIdentity(
        sensitive_meta_id=original.pk,
        first_name="Ada",
        last_name="Lovelace",
        dob=date(1980, 1, 2),
        examination_date=date(2026, 9, 21),
        reviewed_by="test-reviewer",
        review_reference="test-reviewed-source",
    )
    assert (
        migrate_identity_group(original, reviewed={original.pk: review}, apply=False)
        == 1
    )
    original.refresh_from_db()
    assert original.patient_hash == patient_hash
    assert migrate_identity_group(original, reviewed={original.pk: review}) == 1
    original.refresh_from_db()
    assert original.patient_hash != patient_hash
    assert (
        original.pseudo_patient_id == patient_id
        and original.pseudo_examination_id == exam_id
    )
    assert original.patient_first_name is None and original.patient_dob is None
    assert migrate_identity_group(original, reviewed={original.pk: review}) == 0


@pytest.mark.django_db
def test_group_failure_rolls_back_every_link(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old-test-identity-salt"
    with override_settings(DJANGO_SALT=old.decode()):
        original = make_identity()
    old_hash = original.patient_hash
    PatientExamination.objects.create(
        patient_id=original.pseudo_patient_id, hash="unmapped-examination"
    )
    ring = write_ring(tmp_path, b"new-test-salt", (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    with pytest.raises(ValueError, match="Every linked examination"):
        migrate_identity_group(original)
    original.refresh_from_db()
    assert original.patient_hash == old_hash
    assert Patient.objects.get(pk=original.pseudo_patient_id).patient_hash == old_hash


@pytest.mark.django_db
def test_rotation_rejects_inconsistent_metadata_link_instead_of_leaving_it_behind(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old-test-identity-salt"
    with override_settings(DJANGO_SALT=old.decode()):
        original = make_identity()
        duplicate = SensitiveMeta.objects.create(
            center=original.center,
            patient_first_name="Ada",
            patient_last_name="Lovelace",
            patient_dob=original.patient_dob,
            examination_date=original.examination_date,
        )
    old_hash = original.patient_hash
    SensitiveMeta.objects.filter(pk=duplicate.pk).update(
        patient_hash="inconsistent-reference"
    )
    ring = write_ring(tmp_path, b"new-test-salt", (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    with pytest.raises(ValueError, match="inconsistent"):
        migrate_identity_group(original)
    original.refresh_from_db()
    assert original.patient_hash == old_hash
    assert Patient.objects.get(pk=original.pseudo_patient_id).patient_hash == old_hash


@pytest.mark.django_db
def test_identity_command_reports_blocked_erased_group_without_disclosing_source(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = b"old-test-identity-salt"
    with override_settings(DJANGO_SALT=old.decode()):
        original = make_identity()
    old_hash = original.patient_hash
    SensitiveMeta.objects.filter(pk=original.pk).update(
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        direct_identifiers_cleared_at=timezone.now(),
    )
    ring = write_ring(tmp_path, b"new-test-salt", (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    output = StringIO()
    with pytest.raises(CommandError, match="incomplete"):
        call_command("rotate_identity_salt", apply=True, stdout=output)
    assert '"blocked_groups": 1' in output.getvalue()
    assert "Ada" not in output.getvalue() and "Lovelace" not in output.getvalue()
    original.refresh_from_db()
    assert original.patient_hash == old_hash


@pytest.mark.django_db
def test_examiner_rotation_preserves_links_and_rejects_conflicting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.models.administration.person.examiner.examiner import Examiner
    from endoreg_db.services.secret_rotation.identity import (
        ReviewedExaminer,
        migrate_reviewed_examiner,
    )

    center = Center.objects.create(name="examiner-rotation-center")
    old = b"old-test-identity-salt"
    with override_settings(DJANGO_SALT=old.decode()):
        examiner, _ = Examiner.custom_get_or_create("Ada", "Lovelace", center)
    before = examiner.pk, examiner.first_name, examiner.last_name
    ring = write_ring(tmp_path, b"new-test-salt", (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    review = ReviewedExaminer(
        examiner_id=examiner.pk,
        first_name="Ada",
        last_name="Lovelace",
        reviewed_by="test-reviewer",
        review_reference="test-reviewed-source",
    )
    migrate_reviewed_examiner(review, apply=False)
    examiner.refresh_from_db()
    assert examiner.identity_salt_fingerprint == salt_fingerprint(old)
    migrate_reviewed_examiner(review, apply=True)
    examiner.refresh_from_db()
    assert (examiner.pk, examiner.first_name, examiner.last_name) == before
    repeated, created = Examiner.custom_get_or_create("Ada", "Lovelace", center)
    assert repeated.pk == examiner.pk and not created
    Examiner.objects.filter(pk=examiner.pk).update(identity_fingerprint="invalid")
    with pytest.raises(ValueError, match="evidence"):
        migrate_reviewed_examiner(review, apply=True)


@pytest.mark.django_db(transaction=True)
def test_concurrent_imports_during_salt_rotation_keep_one_identity(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL advisory locks")
    old = b"old-test-identity-salt"
    with override_settings(DJANGO_SALT=old.decode()):
        original = make_identity()
    ring = write_ring(tmp_path, b"new-test-identity-salt", (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))
    barrier = Barrier(3)

    def create() -> tuple[int | None, int | None]:
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            row = SensitiveMeta.objects.create(
                center_id=original.center_id,
                patient_first_name="Ada",
                patient_last_name="Lovelace",
                patient_dob=timezone.make_aware(datetime(1980, 1, 2)),
                examination_date=date(2026, 9, 21),
            )
            return row.pseudo_patient_id, row.pseudo_examination_id
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=3) as workers:
        results = [
            job.result(timeout=30) for job in [workers.submit(create) for _ in range(3)]
        ]
    assert set(results) == {
        (original.pseudo_patient_id, original.pseudo_examination_id)
    }
    original.refresh_from_db()
    assert original.identity_salt_fingerprint == salt_fingerprint(
        b"new-test-identity-salt"
    )
