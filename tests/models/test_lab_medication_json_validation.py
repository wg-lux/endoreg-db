from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models import (
    LabValue,
    Medication,
    Patient,
    PatientLabValue,
    PatientMedication,
    Unit,
)


def _patient() -> Patient:
    return Patient.objects.create(
        first_name="Boundary",
        last_name="Patient",
        patient_hash="boundary-patient-json-contract",
    )


@pytest.mark.django_db
def test_lab_value_default_normal_range_round_trips_canonically() -> None:
    lab_value = LabValue.objects.create(
        name="boundary-normal-range",
        default_normal_range={
            "max": 18,
            "min": 12,
            "female": {"max": 16, "min": 10},
        },
    )

    lab_value.refresh_from_db()

    assert lab_value.default_normal_range == {
        "min": 12.0,
        "max": 18.0,
        "female": {"min": 10.0, "max": 16.0},
    }


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"minimum": 1.0},
        {"min": "12"},
        {"min": float("nan")},
        {"male": {"unexpected": 1.0}},
    ],
)
def test_lab_value_normal_range_rejects_invalid_payloads(payload: object) -> None:
    model = LabValue(name="invalid-normal-range", default_normal_range=payload)

    with pytest.raises(ValidationError) as exc_info:
        model.clean()

    assert "default_normal_range" in exc_info.value.message_dict


@pytest.mark.django_db
def test_patient_lab_value_normal_range_round_trips_canonically() -> None:
    lab_value = LabValue.objects.create(name="boundary-patient-normal-range")
    record = PatientLabValue.objects.create(
        lab_value=lab_value,
        normal_range={"max": 5, "min": 0},
    )

    record.refresh_from_db()

    assert record.normal_range == {"min": 0.0, "max": 5.0}
    assert "0.0 - 5.0" in str(record)


@pytest.mark.parametrize(
    "payload",
    [[], None, {"min": "0"}, {"other": {"min": 0.0, "extra": 1.0}}],
)
def test_patient_lab_value_normal_range_rejects_invalid_payloads(
    payload: object,
) -> None:
    model = PatientLabValue(normal_range=payload)

    with pytest.raises(ValidationError) as exc_info:
        model.clean()

    assert "normal_range" in exc_info.value.message_dict


@pytest.mark.django_db
def test_patient_lab_value_builds_valid_uniform_fallback_distribution() -> None:
    patient = Patient.objects.create(
        first_name="Distribution",
        last_name="Patient",
        patient_hash="patient-lab-value-distribution-fallback",
        dob=date(1980, 1, 1),
    )
    lab_value = LabValue.objects.create(
        name="distribution-fallback-lab-value",
        default_normal_range={"min": 1.0, "max": 3.0},
    )
    record = PatientLabValue(
        patient=patient,
        lab_value=lab_value,
        normal_range={"min": 1.0, "max": 3.0},
    )

    with pytest.warns(UserWarning):
        generated = record.set_value_by_distribution(save=False)

    assert isinstance(generated, float)
    assert 1.0 <= generated <= 3.0
    assert record.value == generated


@pytest.mark.django_db
def test_patient_medication_dosage_round_trips_canonical_json() -> None:
    unit = Unit.objects.create(name="boundary-dose-unit")
    medication = Medication.objects.create(
        name="boundary-dose-medication", default_unit=unit
    )
    record = PatientMedication.objects.create(
        patient=_patient(),
        medication=medication,
        dosage={"morning": [500, {"with_food": True}]},
    )

    record.refresh_from_db()

    assert record.dosage == {"morning": [500, {"with_food": True}]}


@pytest.mark.parametrize(
    "payload",
    [
        ("tuple",),
        {1: "non-string-key"},
        {"dose": Path("/tmp/dose")},
        {"dose": float("inf")},
    ],
)
def test_patient_medication_dosage_rejects_invalid_payloads(payload: object) -> None:
    model = PatientMedication(dosage=payload)

    with pytest.raises(ValidationError) as exc_info:
        model.clean()

    assert "dosage" in exc_info.value.message_dict
