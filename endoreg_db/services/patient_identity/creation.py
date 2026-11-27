"""Serialize hash-based patient creation while preserving established identities."""

from __future__ import annotations

import logging

from django.db import transaction

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import (
    Patient,
    PatientGenderInput,
    build_pseudo_patient_profile,
    validate_pseudo_patient_creation_input,
)
from endoreg_db.utils.hashs import get_patient_uuid

logger = logging.getLogger("patient")


@transaction.atomic
def get_or_create_pseudo_patient(
    *,
    patient_hash: str,
    center: Center | None,
    gender: PatientGenderInput,
    birth_month: int | None,
    birth_year: int | None,
) -> tuple[Patient, bool]:
    if center is not None:
        if center.pk is None or center._state.adding:
            raise ValueError("Patient creation requires a persisted center")
        center = Center.objects.select_for_update().get(pk=center.pk)
    existing = Patient.get_pseudo_patient_by_hash(patient_hash, center)
    if existing is not None:
        logger.info("Reusing pseudonymous patient", extra={"patient_id": existing.pk})
        return existing, False

    creation_input = validate_pseudo_patient_creation_input(
        center=center, gender=gender, birth_month=birth_month, birth_year=birth_year
    )
    patient_uuid = get_patient_uuid(
        patient_hash, center_key=creation_input.center.center_key
    )
    profile = build_pseudo_patient_profile(creation_input, patient_hash=patient_hash)
    patient = Patient.objects.create(
        uuid=patient_uuid,
        first_name=profile.first_name,
        last_name=profile.last_name,
        dob=profile.dob,
        gender=profile.gender,
        center=creation_input.center,
        patient_hash=patient_hash,
        is_real_person=False,
    )
    return patient, True
