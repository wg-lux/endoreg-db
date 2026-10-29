from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.administration.person.patient.patient_hash_alias import (
    PatientHashAlias,
)
from endoreg_db.services.patient_identity.hash_aliases import (
    record_patient_hash_alias,
    resolve_patient_hash,
)

pytestmark = pytest.mark.django_db


def test_retained_hash_resolves_after_rotation_and_repeat() -> None:
    center = Center.objects.create(name="alias-center", center_key="alias-center")
    patient = Patient.objects.create(center=center, patient_hash="a" * 64)
    alias = record_patient_hash_alias(
        patient=patient, center=center, patient_hash="a" * 64, salt_fingerprint="b" * 64
    )
    Patient.objects.filter(pk=patient.pk).update(patient_hash="c" * 64)
    assert resolve_patient_hash(center=center, patient_hash="a" * 64) == patient
    assert resolve_patient_hash(center=center, patient_hash="c" * 64) == patient
    assert (
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="b" * 64,
        )
        == alias
    )
    assert PatientHashAlias.objects.count() == 1


def test_pseudonym_entrypoint_resolves_alias_and_rejects_real_patient() -> None:
    center = Center.objects.create(name="alias-center", center_key="alias-center")
    patient = Patient.objects.create(
        center=center, patient_hash="a" * 64, is_real_person=False
    )
    record_patient_hash_alias(
        patient=patient, center=center, patient_hash="a" * 64, salt_fingerprint="b" * 64
    )
    Patient.objects.filter(pk=patient.pk).update(patient_hash="c" * 64)
    assert Patient.get_pseudo_patient_by_hash("a" * 64, center) == patient
    resolved, created = Patient.get_or_create_pseudo_patient_by_hash("a" * 64, center)
    assert resolved == patient and not created
    Patient.objects.filter(pk=patient.pk).update(is_real_person=True)
    with pytest.raises(ValueError, match="real person"):
        Patient.get_pseudo_patient_by_hash("a" * 64, center)


def test_center_isolation_and_ownership_changes_fail_closed() -> None:
    center = Center.objects.create(name="alias-center", center_key="alias-center")
    other = Center.objects.create(name="other-center", center_key="other-center")
    patient = Patient.objects.create(center=center, patient_hash="a" * 64)
    record_patient_hash_alias(
        patient=patient, center=center, patient_hash="a" * 64, salt_fingerprint="b" * 64
    )
    assert resolve_patient_hash(center=other, patient_hash="a" * 64) is None
    with pytest.raises(ValueError, match="different center"):
        record_patient_hash_alias(
            patient=patient,
            center=other,
            patient_hash="a" * 64,
            salt_fingerprint="b" * 64,
        )
    Patient.objects.filter(pk=patient.pk).update(center=other)
    with pytest.raises(ValueError, match="ownership"):
        resolve_patient_hash(center=center, patient_hash="a" * 64)


def test_alias_rollback_conflicts_and_database_uniqueness() -> None:
    center = Center.objects.create(name="alias-center", center_key="alias-center")
    patient = Patient.objects.create(center=center, patient_hash="a" * 64)
    with pytest.raises(RuntimeError), transaction.atomic():
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="b" * 64,
        )
        raise RuntimeError("Abort rotation")
    assert not PatientHashAlias.objects.exists()
    record_patient_hash_alias(
        patient=patient, center=center, patient_hash="a" * 64, salt_fingerprint="b" * 64
    )
    with pytest.raises(ValueError, match="evidence conflicts"):
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="c" * 64,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        PatientHashAlias.objects.bulk_create(
            [
                PatientHashAlias(
                    patient=patient,
                    center=center,
                    patient_hash="a" * 64,
                    salt_fingerprint="b" * 64,
                )
            ]
        )
    Patient.objects.create(center=center, patient_hash="a" * 64)
    with pytest.raises(ValueError, match="conflicting patients"):
        resolve_patient_hash(center=center, patient_hash="a" * 64)


@pytest.mark.parametrize("value", ["", "a", "A" * 64, "a" * 63 + "\n"])
def test_invalid_hash_rejected(value: str) -> None:
    center = Center.objects.create(name="alias-center", center_key="alias-center")
    with pytest.raises(ValueError, match="SHA-256"):
        resolve_patient_hash(center=center, patient_hash=value)


def test_unknown_hash_and_invalid_salt_evidence_rejected() -> None:
    center = Center.objects.create(name="alias-center", center_key="alias-center")
    patient = Patient.objects.create(center=center, patient_hash="a" * 64)
    assert resolve_patient_hash(center=center, patient_hash="d" * 64) is None
    with pytest.raises(ValueError, match="current hash"):
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="c" * 64,
            salt_fingerprint="b" * 64,
        )
    with pytest.raises(ValidationError):
        record_patient_hash_alias(
            patient=patient,
            center=center,
            patient_hash="a" * 64,
            salt_fingerprint="invalid",
        )
    assert not PatientHashAlias.objects.exists()


def test_opaque_current_hash_requires_opt_in_and_is_center_scoped() -> None:
    center = Center.objects.create(name="opaque-center", center_key="opaque-center")
    other = Center.objects.create(name="other-center", center_key="other-center")
    patient = Patient.objects.create(
        center=center, patient_hash="legacy-id", is_real_person=False
    )
    Patient.objects.create(center=other, patient_hash="legacy-id", is_real_person=False)
    with pytest.raises(ValueError, match="SHA-256"):
        resolve_patient_hash(center=center, patient_hash="legacy-id")
    assert (
        resolve_patient_hash(
            center=center, patient_hash="legacy-id", allow_opaque_current_hash=True
        )
        == patient
    )
    assert Patient.get_pseudo_patient_by_hash("legacy-id", center) == patient
    Patient.objects.create(
        center=center, patient_hash="legacy-id", is_real_person=False
    )
    with pytest.raises(ValueError, match="conflicting patients"):
        Patient.get_pseudo_patient_by_hash("legacy-id", center)


@pytest.mark.parametrize("patient_hash", ["", "  "])
def test_opaque_lookup_still_rejects_empty_hash(patient_hash: str) -> None:
    center = Center.objects.create(name="opaque-center", center_key="opaque-center")
    with pytest.raises(ValueError, match="Patient hash is required"):
        resolve_patient_hash(
            center=center, patient_hash=patient_hash, allow_opaque_current_hash=True
        )


def test_opaque_lookup_still_requires_persisted_center() -> None:
    with pytest.raises(ValueError, match="persisted center"):
        resolve_patient_hash(
            center=Center(name="unsaved"),
            patient_hash="legacy-id",
            allow_opaque_current_hash=True,
        )
