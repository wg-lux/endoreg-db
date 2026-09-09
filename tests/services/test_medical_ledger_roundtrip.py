from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any, cast
from uuid import uuid4

import pytest

from endoreg_db.models import (
    Disease,
    DiseaseClassification,
    DiseaseClassificationChoice,
    Event,
    EventClassification,
    EventClassificationChoice,
    LabValue,
    Medication,
    MedicationIntakeTime,
    Patient,
    PatientDisease,
    PatientEvent,
    PatientLabSample,
    PatientLabSampleType,
    PatientLabValue,
    PatientMedication,
    PatientMedicationSchedule,
    Unit,
)
from endoreg_db.services.medical_ledger import (
    MedicalLedgerReferenceConflict,
    build_patient_medical_ledger_for_patient,
    create_patient_medical_ledger,
)
from lx_dtypes.models.ledger.medical import PatientMedicalLedger
from lx_dtypes.models.ledger.medical.Write import PatientMedicalLedgerCreate


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


def _terminology() -> dict[str, str]:
    token = uuid4().hex
    disease = Disease.objects.create(name=f"disease-{token}")
    disease_classification = DiseaseClassification.objects.create(
        name=f"disease-classification-{token}",
        disease=disease,
    )
    disease_choice = DiseaseClassificationChoice.objects.create(
        name=f"disease-choice-{token}",
        disease_classification=disease_classification,
    )
    event = Event.objects.create(name=f"event-{token}")
    event_classification = EventClassification.objects.create(
        name=f"event-classification-{token}",
        event=event,
    )
    event_choice = EventClassificationChoice.objects.create(
        name=f"event-choice-{token}",
        event_classification=event_classification,
    )
    unit = Unit.objects.create(name=f"unit-{token}", abbreviation="mg")
    lab_value = LabValue.objects.create(
        name=f"lab-{token}",
        default_unit=unit,
    )
    sample_type = PatientLabSampleType.objects.create(name=f"sample-{token}")
    medication = Medication.objects.create(
        name=f"medication-{token}",
        default_unit=unit,
    )
    intake_time = MedicationIntakeTime.objects.create(
        name=f"intake-{token}",
        time=time(8, 0),
    )
    return {
        "disease": disease.name,
        "disease_choice": disease_choice.name,
        "event": event.name,
        "event_choice": event_choice.name,
        "unit": unit.name,
        "lab_value": lab_value.name,
        "sample_type": sample_type.name,
        "medication": medication.name,
        "intake_time": intake_time.name,
    }


def _payload(patient: Patient, terms: dict[str, str]) -> PatientMedicalLedgerCreate:
    return PatientMedicalLedgerCreate.model_validate(
        {
            "patient": str(_pk(patient)),
            "diseases": [
                {
                    "disease": terms["disease"],
                    "classification_choices": [terms["disease_choice"]],
                    "start_date": "2024-01-01",
                    "subcategories": {
                        "source": {
                            "choices": ["roundtrip"],
                            "default": "roundtrip",
                            "required": True,
                        }
                    },
                }
            ],
            "events": [
                {
                    "event": terms["event"],
                    "date_start": "2024-02-01",
                    "date_end": "2024-02-02",
                    "description": "Clinical event",
                    "classification_choice": terms["event_choice"],
                }
            ],
            "lab_samples": [
                {
                    "sample_type": terms["sample_type"],
                    "date": "2024-03-01T09:00:00Z",
                    "values": [
                        {
                            "lab_value": terms["lab_value"],
                            "value": 4.25,
                            "timestamp": "2024-03-01T09:05:00Z",
                            "normal_range": {"min": 0.0, "max": 5.0},
                            "unit": terms["unit"],
                        }
                    ],
                }
            ],
            "lab_values": [
                {
                    "lab_value": terms["lab_value"],
                    "value_str": "negative",
                    "timestamp": "2024-03-02T10:00:00Z",
                    "unit": terms["unit"],
                }
            ],
            "medications": [
                {
                    "medication": terms["medication"],
                    "intake_times": [terms["intake_time"]],
                    "unit": terms["unit"],
                    "dosage": {"morning": 500},
                    "active": True,
                }
            ],
            "medication_schedules": [{"medication_indices": [0]}],
        }
    )


def _without_projection_time(value: object) -> object:
    if isinstance(value, list):
        return [_without_projection_time(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        return {
            key: _without_projection_time(item)
            for key, item in cast(dict[str, object], value).items()
            if key != "created_at"
        }
    return value


@pytest.mark.django_db
def test_complete_medical_ledger_create_reload_and_projection_roundtrip() -> None:
    patient = Patient.objects.create(
        first_name="Roundtrip",
        last_name="Patient",
        patient_hash=f"roundtrip-{uuid4().hex}",
    )
    terms = _terminology()

    created = create_patient_medical_ledger(
        patient=patient,
        payload=_payload(patient, terms),
        idempotency_key="roundtrip-create",
    ).ledger
    reloaded = build_patient_medical_ledger_for_patient(
        Patient.objects.get(pk=patient.pk)
    )
    revalidated = PatientMedicalLedger.model_validate(reloaded.model_dump(mode="json"))

    assert len(created.diseases) == len(revalidated.diseases) == 1
    assert len(created.events) == len(revalidated.events) == 1
    assert len(created.lab_samples) == len(revalidated.lab_samples) == 1
    assert len(created.lab_values) == len(revalidated.lab_values) == 2
    assert len(created.medications) == len(revalidated.medications) == 1
    assert len(created.medication_schedules) == 1
    assert _without_projection_time(
        created.model_dump(mode="json")
    ) == _without_projection_time(revalidated.model_dump(mode="json"))
    assert created.uuid == revalidated.uuid
    assert [item.uuid for item in created.medications] == [
        item.uuid for item in revalidated.medications
    ]

    assert PatientDisease.objects.get(patient=patient).start_date == date(2024, 1, 1)
    assert PatientEvent.objects.get(patient=patient).date_end == date(2024, 2, 2)
    sample = PatientLabSample.objects.get(patient=patient)
    assert sample.date == datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
    sample_value = PatientLabValue.objects.get(patient=patient, sample=sample)
    assert sample_value.value == 4.25
    assert sample_value.timestamp == datetime(2024, 3, 1, 9, 5, tzinfo=UTC)
    direct_value = PatientLabValue.objects.get(patient=patient, sample=None)
    assert direct_value.value_str == "negative"
    medication = PatientMedication.objects.get(patient=patient)
    assert medication.dosage == {"morning": 500}
    schedule = PatientMedicationSchedule.objects.get(patient=patient)
    assert list(schedule.medication.all()) == [medication]


@pytest.mark.django_db
def test_complete_medical_ledger_rolls_back_on_late_reference_conflict() -> None:
    patient = Patient.objects.create(
        first_name="Rollback",
        last_name="Patient",
        patient_hash=f"rollback-{uuid4().hex}",
    )
    terms = _terminology()
    payload_data = _payload(patient, terms).model_dump(mode="python")
    cast(list[dict[str, object]], payload_data["lab_values"])[0]["lab_value"] = (
        "missing-lab-value"
    )
    payload = PatientMedicalLedgerCreate.model_validate(payload_data)

    with pytest.raises(MedicalLedgerReferenceConflict) as caught:
        create_patient_medical_ledger(
            patient=patient,
            payload=payload,
            idempotency_key="roundtrip-rollback",
        )

    assert caught.value.field_name == "lab_value"
    assert not PatientDisease.objects.filter(patient=patient).exists()
    assert not PatientEvent.objects.filter(patient=patient).exists()
    assert not PatientLabSample.objects.filter(patient=patient).exists()
    assert not PatientLabValue.objects.filter(patient=patient).exists()
    assert not PatientMedication.objects.filter(patient=patient).exists()
    assert not PatientMedicationSchedule.objects.filter(patient=patient).exists()
