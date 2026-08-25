from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4

import pytest
from django.utils import timezone

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.gender import Gender
from endoreg_db.schemas.sensitive_meta_verification import (
    SensitiveMetaVerificationCommand,
)
from endoreg_db.services.sensitive_meta_verification import (
    update_sensitive_meta_verification,
)


def _create_sensitive_meta() -> SensitiveMeta:
    suffix = uuid4().hex[:8]
    patient = Patient.objects.create(
        first_name="Pseudo",
        last_name="Patient",
        patient_hash=f"verification-patient-{suffix}",
    )
    examination = PatientExamination.objects.create(patient=patient)
    return SensitiveMeta.objects.create(
        patient_first_name="Max",
        patient_last_name="Mustermann",
        patient_dob=timezone.make_aware(datetime(1994, 3, 21, 0, 0)),
        examination_date=date(2025, 11, 27),
        examination_time=time(9, 30),
        casenumber=f"VERIFY-{suffix}",
        pseudo_patient=patient,
        pseudo_examination=examination,
        patient_gender=Gender.objects.create(name=f"verification-gender-{suffix}"),
        center=Center.objects.create(name=f"verification-center-{suffix}"),
    )


@pytest.mark.django_db
def test_update_sensitive_meta_verification_preserves_omitted_flag() -> None:
    sensitive_meta = _create_sensitive_meta()

    state = sensitive_meta.state_safe
    state.dob_verified = True
    state.names_verified = True
    state.save()

    result = update_sensitive_meta_verification(
        sensitive_meta_id=sensitive_meta.pk,
        command=SensitiveMetaVerificationCommand(names_verified=False),
    )

    state.refresh_from_db()
    assert state.dob_verified is True
    assert state.names_verified is False
    assert result.dob_verified is True
    assert result.names_verified is False
    assert result.is_verified is False


@pytest.mark.django_db
def test_update_sensitive_meta_verification_recreates_missing_state() -> None:
    sensitive_meta = _create_sensitive_meta()
    sensitive_meta.state_safe.delete()

    result = update_sensitive_meta_verification(
        sensitive_meta_id=sensitive_meta.pk,
        command=SensitiveMetaVerificationCommand(dob_verified=True),
    )

    sensitive_meta.refresh_from_db()
    assert sensitive_meta.state_safe.dob_verified is True
    assert sensitive_meta.state_safe.names_verified is False
    assert result.is_verified is False
