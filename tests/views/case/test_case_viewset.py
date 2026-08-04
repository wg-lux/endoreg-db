from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from endoreg_db.models import (
    Case,
    Examination,
    LabValue,
    Medication,
    Patient,
    PatientExamination,
    PatientLabSample,
    PatientLabSampleType,
    PatientLabValue,
    PatientMedication,
    PatientMedicationSchedule,
    Unit,
)


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


@pytest.mark.django_db
def test_case_roundtrip_persists_patient_clinical_graph(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"case-patient-{uuid4().hex}",
        first_name="Case",
        last_name="Patient",
    )
    examination = PatientExamination.objects.create(
        patient=patient,
        hash=f"case-examination-{uuid4().hex}",
    )
    unit = Unit.objects.create(name=f"unit-{uuid4().hex}")
    medication = Medication.objects.create(
        name=f"medication-{uuid4().hex}", default_unit=unit
    )
    patient_medication = PatientMedication.objects.create(
        patient=patient,
        medication=medication,
        medication_indication=None,
    )
    medication_schedule = PatientMedicationSchedule.objects.create(patient=patient)
    medication_schedule.medication.add(patient_medication)
    sample_type = PatientLabSampleType.objects.create(name=f"sample-{uuid4().hex}")
    lab_sample = PatientLabSample.objects.create(
        patient=patient,
        sample_type=sample_type,
        date=timezone.now(),
    )
    lab_definition = LabValue.objects.create(name=f"lab-{uuid4().hex}")
    lab_value = PatientLabValue.objects.create(
        patient=patient,
        sample=lab_sample,
        lab_value=lab_definition,
        value=1.0,
    )

    response = api_client.post(
        "/api/cases/",
        data={
            "patient": _pk(patient),
            "admission_date": "2026-07-22T08:00:00Z",
            "patient_examination_ids": [_pk(examination)],
            "patient_medication_ids": [_pk(patient_medication)],
            "patient_medication_schedule_ids": [_pk(medication_schedule)],
            "patient_lab_sample_ids": [_pk(lab_sample)],
            "patient_lab_value_ids": [_pk(lab_value)],
        },
        format="json",
    )

    assert response.status_code == 201
    body = cast(dict[str, Any], response.data)
    assert body["case_id"]
    assert body["patient"] == _pk(patient)
    assert body["patient_medications"] == [_pk(patient_medication)]
    assert body["patient_medication_schedules"] == [_pk(medication_schedule)]
    assert body["patient_lab_samples"] == [_pk(lab_sample)]
    assert body["patient_lab_values"] == [_pk(lab_value)]
    assert [row["id"] for row in body["patient_examinations"]] == [_pk(examination)]

    case_id = body["case_id"]
    detail_response = api_client.get(f"/api/cases/{case_id}/")
    assert detail_response.status_code == 200
    assert cast(dict[str, Any], detail_response.data)["case_id"] == case_id

    list_response = api_client.get(
        "/api/cases/", {"patient_examination_id": _pk(examination)}
    )
    assert list_response.status_code == 200
    rows = cast(list[dict[str, Any]], list_response.data)
    assert [row["case_id"] for row in rows] == [case_id]


@pytest.mark.django_db
def test_case_rejects_relationship_from_another_patient(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"case-owner-{uuid4().hex}",
        first_name="Case",
        last_name="Owner",
    )
    other_patient = Patient.objects.create(
        patient_hash=f"case-other-{uuid4().hex}",
        first_name="Other",
        last_name="Patient",
    )
    other_examination = PatientExamination.objects.create(
        patient=other_patient,
        hash=f"case-other-examination-{uuid4().hex}",
    )

    response = api_client.post(
        "/api/cases/",
        data={
            "patient": _pk(patient),
            "admission_date": "2026-07-22T08:00:00Z",
            "patient_examination_ids": [_pk(other_examination)],
        },
        format="json",
    )

    assert response.status_code == 400
    assert "another patient" in str(response.data)


@pytest.mark.django_db
def test_case_and_patient_examination_are_created_atomically(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"case-atomic-{uuid4().hex}",
        first_name="Atomic",
        last_name="Patient",
    )
    examination = Examination.objects.create(name=f"atomic-exam-{uuid4().hex}")

    response = api_client.post(
        "/api/cases/create-with-examination/",
        data={
            "admission_date": "2026-07-22T08:00:00Z",
            "patient_examination": {
                "patient": patient.patient_hash,
                "examination": examination.name,
                "date_start": "2026-07-22",
            },
        },
        format="json",
    )

    assert response.status_code == 201
    body = cast(dict[str, Any], response.data)
    case_body = cast(dict[str, Any], body["case"])
    patient_examination_body = cast(dict[str, Any], body["patient_examination"])
    persisted_case = Case.objects.get(case_id=case_body["case_id"])
    assert persisted_case.patient == patient
    assert list(persisted_case.patient_examinations.values_list("id", flat=True)) == [
        patient_examination_body["id"]
    ]
