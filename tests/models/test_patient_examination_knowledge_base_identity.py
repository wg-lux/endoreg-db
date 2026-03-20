from __future__ import annotations

import pytest

from endoreg_db.models import Examination, Patient, PatientExamination
from endoreg_db.serializers.patient_examination.patient_examination import (
    PatientExaminationSerializer,
)


@pytest.mark.django_db
def test_patient_examination_save_stamps_knowledge_base_identity(monkeypatch) -> None:
    patient = Patient.objects.create(
        patient_hash="kb-patient-save",
        first_name="KB",
        last_name="Save",
    )

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        lambda: ("report_template_examples", "0.1.0"),
    )

    patient_examination = PatientExamination.objects.create(patient=patient)

    assert patient_examination.knowledge_base_module == "report_template_examples"
    assert patient_examination.knowledge_base_version == "0.1.0"


@pytest.mark.django_db
def test_patient_examination_save_preserves_existing_knowledge_base_identity(
    monkeypatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="kb-patient-preserve",
        first_name="KB",
        last_name="Preserve",
    )

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        lambda: ("report_template_examples", "0.1.0"),
    )

    patient_examination = PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module="sealed_module",
        knowledge_base_version="2026.03",
    )

    assert patient_examination.knowledge_base_module == "sealed_module"
    assert patient_examination.knowledge_base_version == "2026.03"


@pytest.mark.django_db
def test_patient_examination_serializer_create_stamps_knowledge_base_identity(
    monkeypatch,
) -> None:
    Patient.objects.create(
        patient_hash="serializer-patient",
        first_name="Serializer",
        last_name="Patient",
    )
    Examination.objects.create(name="serializer_exam")

    monkeypatch.setattr(
        "endoreg_db.services.knowledge_base_identity.get_configured_knowledge_base_identity",
        lambda: ("report_template_examples", "0.1.0"),
    )

    serializer = PatientExaminationSerializer(
        data={
            "patient": "serializer-patient",
            "examination": "serializer_exam",
        }
    )

    assert serializer.is_valid(), serializer.errors
    patient_examination = serializer.save()

    assert patient_examination.knowledge_base_module == "report_template_examples"
    assert patient_examination.knowledge_base_version == "0.1.0"
