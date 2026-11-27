from __future__ import annotations

import pytest

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.services.patient_identity.hash_aliases import record_patient_hash_alias

pytestmark = pytest.mark.django_db


@pytest.fixture
def patient() -> Patient:
    center = Center.objects.create(name="exam-alias", center_key="exam-alias")
    patient = Patient.objects.create(
        center=center, patient_hash="a" * 64, is_real_person=False
    )
    record_patient_hash_alias(
        patient=patient, center=center, patient_hash="a" * 64, salt_fingerprint="b" * 64
    )
    Patient.objects.filter(pk=patient.pk).update(patient_hash="c" * 64)
    patient.refresh_from_db()
    return patient


@pytest.mark.parametrize("patient_hash", ["a" * 64, "c" * 64])
@pytest.mark.parametrize("existing", [False, True])
def test_current_and_historical_patient_hash_reuse_one_examination(
    patient: Patient, patient_hash: str, existing: bool
) -> None:
    assert patient.center is not None
    if existing:
        PatientExamination.objects.create(patient=patient, hash="exam-hash")
    resolved, created = (
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            patient_hash, "exam-hash", center=patient.center
        )
    )
    assert created is not existing
    assert resolved.patient_id == patient.pk
    assert PatientExamination.objects.count() == 1
    assert Patient.objects.count() == 1


@pytest.mark.parametrize("different_center", [False, True])
def test_exam_hash_cannot_override_patient_ownership(
    patient: Patient, different_center: bool
) -> None:
    assert patient.center is not None
    center = (
        Center.objects.create(name="other", center_key="other")
        if different_center
        else patient.center
    )
    other = Patient.objects.create(
        center=center, patient_hash="d" * 64, is_real_person=False
    )
    examination = PatientExamination.objects.create(patient=other, hash="exam-hash")
    with pytest.raises(ValueError, match="different patient"):
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            "a" * 64, "exam-hash", center=patient.center
        )
    examination.refresh_from_db()
    assert examination.patient_id == other.pk
    assert PatientExamination.objects.count() == 1


@pytest.mark.parametrize("failure", ["unknown", "wrong_center", "real", "ambiguous"])
def test_invalid_patient_identity_never_creates_examination(
    patient: Patient, failure: str
) -> None:
    assert patient.center is not None
    center = patient.center
    patient_hash = "a" * 64
    if failure == "unknown":
        patient_hash = "d" * 64
    elif failure == "wrong_center":
        center = Center.objects.create(name="other", center_key="other")
    elif failure == "real":
        Patient.objects.filter(pk=patient.pk).update(is_real_person=True)
    else:
        Patient.objects.create(center=center, patient_hash=patient_hash)
    patient_count = Patient.objects.count()
    with pytest.raises(ValueError):
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            patient_hash, "exam-hash", center=center
        )
    assert not PatientExamination.objects.exists()
    assert Patient.objects.count() == patient_count


@pytest.mark.parametrize("examination_hash", ["", "   "])
def test_empty_examination_hash_rejected(
    patient: Patient, examination_hash: str
) -> None:
    assert patient.center is not None
    with pytest.raises(ValueError, match="Examination hash is required"):
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            "a" * 64, examination_hash, center=patient.center
        )
    assert not PatientExamination.objects.exists()


def test_unpersisted_center_rejected(patient: Patient) -> None:
    with pytest.raises(ValueError, match="persisted center"):
        PatientExamination.get_or_create_pseudo_patient_examination_by_hash(
            "a" * 64, "exam-hash", center=Center(name="unsaved")
        )
    assert not PatientExamination.objects.exists()
