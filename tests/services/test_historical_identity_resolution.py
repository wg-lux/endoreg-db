from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import timezone

from endoreg_db.config.secret_keyring import SecretKeyring
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.patient_identity.hash_aliases import record_patient_hash_alias
from endoreg_db.services.secret_rotation.identity import (
    IdentitySource,
    ReviewedIdentity,
    identity_fingerprint,
    legacy_hash,
    migrate_identity_group,
    salt_fingerprint,
)
from endoreg_db.utils.hashs import get_patient_examination_hash
from tests.conftest import gen_salt_pair
from tests.services.test_identity_salt_rotation import make_identity
from tests.services.test_secret_rotation import write_ring


@pytest.mark.django_db
@pytest.mark.parametrize("same_examination", [True, False])
def test_returning_source_matches_erased_patient_without_restoring_identifiers(
    base_db_data: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_examination: bool,
) -> None:
    old_str, old = gen_salt_pair()
    _, new = gen_salt_pair()

    with override_settings(DJANGO_SALT=old_str):
        original = make_identity()

    center, birth_date = original.center, original.patient_dob
    assert center is not None
    old_hash, exam_id = original.patient_hash, original.pseudo_examination_id
    cleared_at = timezone.now()

    SensitiveMeta.objects.filter(pk=original.pk).update(
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        examination_date=None,
        direct_identifiers_cleared_at=cleared_at,
    )

    ring = write_ring(tmp_path, new, (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))

    exam_date = date(2026, 9, 21) if same_examination else date(2026, 10, 16)
    repeated = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=birth_date,
        examination_date=exam_date,
    )
    repeated.save()
    original.refresh_from_db()

    assert repeated.pseudo_patient_id == original.pseudo_patient_id
    assert repeated.patient_hash == original.patient_hash == old_hash
    assert repeated.identity_salt_fingerprint == salt_fingerprint(old)
    assert original.patient_first_name is None and original.patient_dob is None
    assert original.examination_date is None
    assert original.direct_identifiers_cleared_at == cleared_at
    assert Patient.objects.filter(center=center).count() == 1
    assert PatientExamination.objects.filter(
        patient_id=original.pseudo_patient_id
    ).count() == (1 if same_examination else 2)

    if same_examination:
        assert repeated.pseudo_examination_id == exam_id
    else:
        assert repeated.examination_hash == get_patient_examination_hash(
            first_name="Ada",
            last_name="Lovelace",
            dob=date(1980, 1, 2),
            center_key=center.center_key,
            examination_date=exam_date,
        )

    newcomer = SensitiveMeta.objects.create(
        center=center,
        patient_first_name="Grace",
        patient_last_name="Hopper",
        patient_dob=birth_date,
        examination_date=exam_date,
    )
    assert newcomer.pseudo_patient_id != original.pseudo_patient_id
    assert newcomer.identity_salt_fingerprint == salt_fingerprint(new)

    review = ReviewedIdentity(
        sensitive_meta_id=original.pk,
        first_name="Ada",
        last_name="Lovelace",
        dob=date(1980, 1, 2),
        examination_date=date(2026, 9, 21),
        reviewed_by="test-reviewer",
        review_reference="test-source-review",
    )
    assert migrate_identity_group(original, reviewed={original.pk: review}) == 2
    original.refresh_from_db()
    repeated.refresh_from_db()
    assert original.patient_hash == repeated.patient_hash != old_hash
    assert original.patient_dob is None and original.examination_date is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_fingerprint", ""),
        ("identity_fingerprint", "conflicting"),
        ("identity_salt_fingerprint", "unknown-generation"),
        ("examination_hash", "conflicting"),
    ],
)
def test_historical_source_rejects_missing_conflicting_or_unknown_evidence(
    base_db_data: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    old_str, old = gen_salt_pair()
    _, new = gen_salt_pair()

    with override_settings(DJANGO_SALT=old_str):
        original = make_identity()

    before = (
        Patient.objects.count(),
        PatientExamination.objects.count(),
        SensitiveMeta.objects.count(),
    )
    SensitiveMeta.objects.filter(pk=original.pk).update(
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        examination_date=None,
        direct_identifiers_cleared_at=timezone.now(),
        **{field: value},
    )

    ring = write_ring(tmp_path, new, (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))

    with pytest.raises(ValueError, match="Historical"):
        SensitiveMeta.objects.create(
            center=original.center,
            patient_first_name="Ada",
            patient_last_name="Lovelace",
            patient_dob=original.patient_dob,
            examination_date=original.examination_date,
        )
    assert (
        Patient.objects.count(),
        PatientExamination.objects.count(),
        SensitiveMeta.objects.count(),
    ) == before


@pytest.mark.django_db
def test_legacy_concatenation_collision_cannot_reidentify_erased_patient(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_str, old = gen_salt_pair()
    _, new = gen_salt_pair()

    with override_settings(DJANGO_SALT=old_str):
        original = make_identity()

    # Explicitly stamp the historical legacy concatenated hash to test legacy disambiguation
    assert original.center is not None
    historical_legacy_hash = legacy_hash(
        "Ada", "Lovelace", original.patient_dob, original.center.name, None, old
    )
    historical_exam_hash = legacy_hash(
        "Ada",
        "Lovelace",
        original.patient_dob,
        original.center.name,
        original.examination_date,
        old,
    )
    source = IdentitySource(
        first_name="Ada", last_name="Lovelace", dob=date(1980, 1, 2)
    )
    SensitiveMeta.objects.filter(pk=original.pk).update(
        patient_first_name=None,
        patient_last_name=None,
        patient_dob=None,
        examination_date=None,
        patient_hash=historical_legacy_hash,
        examination_hash=historical_exam_hash,
        identity_fingerprint=identity_fingerprint(source, original.center.pk, old),
        identity_salt_fingerprint=salt_fingerprint(old),
        direct_identifiers_cleared_at=timezone.now(),
    )
    Patient.objects.filter(pk=original.pseudo_patient_id).update(
        patient_hash=historical_legacy_hash
    )
    PatientExamination.objects.filter(pk=original.pseudo_examination_id).update(
        hash=historical_exam_hash
    )

    ring = write_ring(tmp_path, new, (old,), kind="identity")
    monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))

    with pytest.raises(ValueError, match="evidence requires explicit review"):
        SensitiveMeta.objects.create(
            center=original.center,
            patient_first_name="Ad",
            patient_last_name="aLovelace",
            patient_dob=original.patient_dob,
            examination_date=original.examination_date,
            patient_gender=original.patient_gender,
        )

    # Positive control: the rejection above must be caused by the ambiguous
    # name split, not incomplete or internally inconsistent historical evidence.
    returning = SensitiveMeta.objects.create(
        center=original.center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=original.patient_dob,
        examination_date=original.examination_date,
        patient_gender=original.patient_gender,
    )
    assert returning.pseudo_patient_id == original.pseudo_patient_id
    assert returning.pseudo_examination_id == original.pseudo_examination_id
    original.refresh_from_db()
    assert original.patient_first_name is None and original.patient_last_name is None


@pytest.mark.django_db
def test_identity_salt_enrollment_is_scoped_to_each_center(base_db_data: bool) -> None:
    salt_1, _ = gen_salt_pair()
    salt_2, _ = gen_salt_pair()

    with override_settings(DJANGO_SALT=salt_1):
        original = make_identity()

    second_center = Center.objects.create(name="second-salt-center")
    with override_settings(DJANGO_SALT=salt_2):
        other = SensitiveMeta.objects.create(
            center=second_center,
            patient_first_name="Ada",
            patient_last_name="Lovelace",
            patient_dob=original.patient_dob,
        )
    assert other.pseudo_patient_id != original.pseudo_patient_id
    assert other.identity_salt_fingerprint != original.identity_salt_fingerprint


@pytest.mark.django_db
def test_managed_identity_salt_rejects_another_center_before_import_or_migration(
    base_db_data: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = make_identity()
    _, new = gen_salt_pair()
    _, old = gen_salt_pair()

    ring = SecretKeyring(
        active=new,
        retiring=(old,),
        center_id="a-different-center",
        generation=2,
    )
    monkeypatch.setattr(
        "endoreg_db.config.identity_hashing.configured_identity_keyring", lambda: ring
    )
    before = SensitiveMeta.objects.count(), Patient.objects.count()

    with pytest.raises(ValueError, match="does not belong to this center"):
        SensitiveMeta.objects.create(
            center=original.center,
            patient_first_name="Ada",
            patient_last_name="Lovelace",
            patient_dob=original.patient_dob,
        )
    with pytest.raises(ValueError, match="does not belong to this center"):
        migrate_identity_group(original)

    assert (SensitiveMeta.objects.count(), Patient.objects.count()) == before


@pytest.mark.django_db
@pytest.mark.parametrize("managed", [True, False])
def test_retained_alias_prevents_creating_another_patient_without_current_generation_evidence(
    base_db_data: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    old_str, old = gen_salt_pair()
    _, new = gen_salt_pair()

    with override_settings(DJANGO_SALT=old_str):
        original = make_identity()

    assert original.center is not None and original.pseudo_patient is not None
    record_patient_hash_alias(
        patient=original.pseudo_patient,
        center=original.center,
        patient_hash=str(original.patient_hash),
        salt_fingerprint=salt_fingerprint(old),
    )
    Patient.objects.filter(pk=original.pseudo_patient_id).update(patient_hash="f" * 64)

    if managed:
        ring = write_ring(tmp_path, new, (old,), kind="identity")
        monkeypatch.setenv("DJANGO_IDENTITY_SALT_KEYRING_FILE", str(ring))

    before = Patient.objects.count(), SensitiveMeta.objects.count()
    with (
        override_settings(DJANGO_SALT=old_str),
        pytest.raises(ValueError, match="Historical alias requires"),
    ):
        SensitiveMeta.objects.create(
            center=original.center,
            patient_first_name="Ada",
            patient_last_name="Lovelace",
            patient_dob=original.patient_dob,
        )
    assert (Patient.objects.count(), SensitiveMeta.objects.count()) == before


@pytest.mark.django_db
def test_managed_center_identity_keeps_default_examiner_in_own_center(
    base_db_data: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_str, old = gen_salt_pair()

    with override_settings(DJANGO_SALT=old_str):
        original = make_identity()

    assert original.center is not None
    ring = SecretKeyring(active=old, center_id=original.center.center_key, generation=1)
    monkeypatch.setattr(
        "endoreg_db.config.identity_hashing.configured_identity_keyring", lambda: ring
    )

    repeated = SensitiveMeta.objects.create(
        center=original.center,
        patient_first_name="Ada",
        patient_last_name="Lovelace",
        patient_dob=original.patient_dob,
    )
    assert repeated.pseudo_patient_id == original.pseudo_patient_id
    assert set(repeated.examiners.values_list("center_id", flat=True)) == {
        original.center_id
    }
