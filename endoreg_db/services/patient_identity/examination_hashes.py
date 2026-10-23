"""Resolve examination identities through the canonical center-scoped patient lookup."""

from __future__ import annotations

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.examination.examination import Examination
from endoreg_db.models.medical.patient.patient_examination import PatientExamination


@transaction.atomic
def get_or_create_pseudo_patient_examination_by_hash(
    *,
    patient_hash: str,
    examination_hash: str,
    center: Center,
    examination_name: str | None = None,
) -> tuple[PatientExamination, bool]:
    """Reuse aliases without creating patients or accepting conflicting exam ownership."""
    if center.pk is None or center._state.adding:
        raise ValueError("Patient examination resolution requires a persisted center")
    if not examination_hash or not examination_hash.strip():
        raise ValueError("Examination hash is required")
    patient = Patient.get_pseudo_patient_by_hash(patient_hash, center)
    if patient is None:
        raise ValueError("Patient hash has no pseudonym in the requested center")

    patient_examination = PatientExamination.objects.filter(
        hash=examination_hash
    ).first()
    created = False
    if patient_examination is None:
        examination = (
            Examination.objects.get(name=examination_name)
            if examination_name is not None
            else None
        )
        patient_examination, created = PatientExamination.objects.get_or_create(
            hash=examination_hash,
            defaults={"patient": patient, "examination": examination},
        )
    if patient_examination.patient_id != patient.pk:
        raise ValueError("Examination hash belongs to a different patient")
    return patient_examination, created
