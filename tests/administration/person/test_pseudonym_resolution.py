"""Identity conflicts must never silently select a patient during import."""

from datetime import date

import pytest

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("patient_hash", ["", " ", "\t\n"])
def test_empty_identity_never_resolves(patient_hash: str) -> None:
    Patient.objects.create(patient_hash=patient_hash, is_real_person=False)
    with pytest.raises(ValueError, match="hash is required"):
        Patient.get_or_create_pseudo_patient_by_hash(patient_hash)


def test_duplicate_identity_is_rejected_without_mutation() -> None:
    for name in ("First", "Second"):
        Patient.objects.create(
            patient_hash="duplicate", first_name=name, is_real_person=False
        )
    with pytest.raises(ValueError, match="Ambiguous"):
        Patient.get_or_create_pseudo_patient_by_hash("duplicate")
    assert list(
        Patient.objects.filter(patient_hash="duplicate")
        .order_by("pk")
        .values_list("first_name", flat=True)
    ) == ["First", "Second"]


def test_real_patient_is_not_returned_as_pseudonym() -> None:
    patient = Patient.objects.create(patient_hash="real", is_real_person=True)
    with pytest.raises(ValueError, match="real person"):
        Patient.get_or_create_pseudo_patient_by_hash("real")
    patient.refresh_from_db()
    assert patient.is_real_person


def test_center_conflict_preserves_existing_patient() -> None:
    original = Center.objects.create(name="pseudonym-original")
    other = Center.objects.create(name="pseudonym-other")
    patient = Patient.objects.create(
        patient_hash="scoped", center=original, is_real_person=False
    )
    with pytest.raises(ValueError, match="different center"):
        Patient.get_or_create_pseudo_patient_by_hash("scoped", center=other)
    patient.refresh_from_db()
    assert patient.center_id == original.pk


def test_existing_identity_keeps_established_profile() -> None:
    center = Center.objects.create(name="pseudonym-stable")
    patient = Patient.objects.create(
        patient_hash="stable",
        center=center,
        is_real_person=False,
        first_name="Established",
        last_name="Pseudonym",
        dob=date(1980, 2, 12),
    )
    resolved, created = Patient.get_or_create_pseudo_patient_by_hash(
        "stable", center=center
    )
    assert resolved.pk == patient.pk
    assert not created
    assert (resolved.first_name, resolved.last_name, resolved.dob) == (
        "Established",
        "Pseudonym",
        date(1980, 2, 12),
    )
    assert Patient.get_or_create_pseudo_patient_by_hash("stable") == (patient, False)


def test_unknown_identity_lookup_does_not_create_patient() -> None:
    before = Patient.objects.count()
    assert Patient.get_pseudo_patient_by_hash("missing") is None
    assert Patient.objects.count() == before
