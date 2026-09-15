from __future__ import annotations

from datetime import time
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from endoreg_db.models import (
    Center,
    Medication,
    MedicationIntakeTime,
    Patient,
    PatientMedication,
    PatientMedicationSchedule,
    Unit,
)
from endoreg_db.services.medical_ledger import (
    build_patient_medical_ledger_for_patient,
)
from endoreg_db.views.patient.patient import MedicalLedgerContractUnavailable


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


def _create_medication_graph(
    patient: Patient,
) -> tuple[PatientMedication, PatientMedicationSchedule]:
    unit = Unit.objects.create(
        name=f"milligram-{uuid4().hex}",
        abbreviation="mg",
    )
    medication = Medication.objects.create(
        name=f"mesalazine-{uuid4().hex}",
        default_unit=unit,
    )
    intake_time = MedicationIntakeTime.objects.create(
        name=f"daily-morning-{uuid4().hex}",
        time=time(8, 0),
    )
    patient_medication = PatientMedication.objects.create(
        patient=patient,
        medication=medication,
        unit=unit,
        dosage={"morning": 500},
        active=True,
    )
    patient_medication.intake_times.add(intake_time)
    schedule = PatientMedicationSchedule.objects.create(patient=patient)
    schedule.medication.add(patient_medication)
    return patient_medication, schedule


@pytest.mark.django_db
def test_medical_ledger_service_projects_medication_details_and_schedules() -> None:
    patient = Patient.objects.create(
        first_name="Ledger",
        last_name="Patient",
        patient_hash=f"ledger-patient-{uuid4().hex}",
    )
    patient_medication, schedule = _create_medication_graph(patient)
    other_patient = Patient.objects.create(
        first_name="Other",
        last_name="Patient",
        patient_hash=f"other-patient-{uuid4().hex}",
    )
    _create_medication_graph(other_patient)

    ledger = build_patient_medical_ledger_for_patient(patient)

    assert ledger.patient == str(_pk(patient))
    assert len(ledger.medications) == 1
    projected_medication = ledger.medications[0]
    assert projected_medication.external_ids == {
        "endoreg_db": f"PatientMedication:{_pk(patient_medication)}"
    }
    assert projected_medication.medication == patient_medication.medication.name
    assert projected_medication.unit == patient_medication.unit.name
    assert projected_medication.dosage == {"morning": 500}
    assert projected_medication.intake_times == [
        patient_medication.intake_times.get().name
    ]

    assert len(ledger.medication_schedules) == 1
    projected_schedule = ledger.medication_schedules[0]
    assert projected_schedule.external_ids == {
        "endoreg_db": f"PatientMedicationSchedule:{_pk(schedule)}"
    }
    assert [item.uuid for item in projected_schedule.medications] == [
        projected_medication.uuid
    ]


@pytest.mark.django_db
def test_patient_medical_ledger_api_returns_validated_projection(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        first_name="Ada",
        last_name="Lovelace",
        patient_hash=f"api-ledger-patient-{uuid4().hex}",
    )
    patient_medication, schedule = _create_medication_graph(patient)

    response = api_client.get(f"/api/patients/{_pk(patient)}/medical-ledger/")

    assert response.status_code == 200, response.content
    payload = cast(dict[str, Any], response.json())
    assert payload["patient"] == str(_pk(patient))
    assert payload["medications"][0]["external_ids"]["endoreg_db"] == (
        f"PatientMedication:{_pk(patient_medication)}"
    )
    assert payload["medications"][0]["medication"] == patient_medication.medication.name
    assert payload["medication_schedules"][0]["external_ids"]["endoreg_db"] == (
        f"PatientMedicationSchedule:{_pk(schedule)}"
    )
    assert payload["medication_schedules"][0]["medications"][0]["medication"] == (
        patient_medication.medication.name
    )


@pytest.mark.django_db
def test_patient_medical_ledger_api_reports_unavailable_contract(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        first_name="Pending",
        last_name="Release",
        patient_hash=f"pending-ledger-patient-{uuid4().hex}",
    )

    with patch(
        "endoreg_db.views.patient.patient._build_patient_medical_ledger",
        side_effect=MedicalLedgerContractUnavailable(),
    ):
        response = api_client.get(f"/api/patients/{_pk(patient)}/medical-ledger/")

    assert response.status_code == 503
    assert response.json() == {
        "code": "medical-ledger-contract-unavailable",
        "detail": (
            "The installed lx-dtypes package does not provide the medical ledger contract."
        ),
    }


@pytest.mark.django_db
@patch("endoreg_db.views.access_control.resolve_allowed_center_id")
def test_patient_medical_ledger_api_hides_foreign_center(
    mock_allowed_center_id: MagicMock,
    api_client: APIClient,
) -> None:
    visible_center = Center.objects.create(
        name=f"visible-{uuid4().hex}",
        display_name="Visible",
    )
    foreign_center = Center.objects.create(
        name=f"foreign-{uuid4().hex}",
        display_name="Foreign",
    )
    patient = Patient.objects.create(
        first_name="Foreign",
        last_name="Patient",
        center=foreign_center,
        patient_hash=f"foreign-ledger-patient-{uuid4().hex}",
    )
    api_client.force_login(User.objects.create_user(username=f"reader-{uuid4().hex}"))
    mock_allowed_center_id.return_value = _pk(visible_center)

    response = api_client.get(f"/api/patients/{_pk(patient)}/medical-ledger/")

    assert response.status_code == 404
