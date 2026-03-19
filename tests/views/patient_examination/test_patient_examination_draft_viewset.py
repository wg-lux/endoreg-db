from __future__ import annotations

from uuid import uuid4

import pytest

from endoreg_db.models import Patient, PatientExamination


@pytest.mark.django_db
def test_patient_examination_draft_roundtrip(api_client) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-patient-{uuid4().hex}",
        first_name="Draft",
        last_name="Patient",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    url = f"/api/patient-examinations/{patient_examination.id}/draft/"

    response = api_client.get(url)
    assert response.status_code == 200
    assert response.json()["patient_examination_id"] == patient_examination.id
    assert response.json()["draft"] == {}
    assert response.json()["updated_at"] is None

    payload = {
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "payload": {
            "sections": [
                {"name": "findings", "items": ["esophagus_polyp"]},
            ]
        },
    }
    response = api_client.put(url, data=payload, format="json")
    assert response.status_code == 200
    body = response.json()
    assert body["patient_examination_id"] == patient_examination.id
    assert body["draft"] == payload
    assert body["updated_at"] is not None

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == payload
    assert patient_examination.draft_updated_at is not None

    response = api_client.get(url)
    assert response.status_code == 200
    assert response.json()["draft"] == payload


@pytest.mark.django_db
def test_patient_examination_draft_returns_404_for_missing_exam(api_client) -> None:
    response = api_client.get("/api/patient-examinations/999999/draft/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_patient_examination_draft_rejects_non_object_payload(api_client) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-invalid-{uuid4().hex}",
        first_name="Draft",
        last_name="Invalid",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    response = api_client.put(
        f"/api/patient-examinations/{patient_examination.id}/draft/",
        data=["not", "an", "object"],
        format="json",
    )

    assert response.status_code == 400
    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}
    assert patient_examination.draft_updated_at is None


@pytest.mark.django_db
def test_patient_examination_draft_put_overwrites_previous_payload(api_client) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-overwrite-{uuid4().hex}",
        first_name="Draft",
        last_name="Overwrite",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    url = f"/api/patient-examinations/{patient_examination.id}/draft/"

    first_payload = {
        "module_name": "report_template_examples",
        "template_name": "template_a",
        "payload": {"step": 1},
    }
    second_payload = {
        "module_name": "report_template_examples",
        "template_name": "template_b",
        "payload": {"step": 2, "selected": ["colon_polyp"]},
    }

    first_response = api_client.put(url, data=first_payload, format="json")
    assert first_response.status_code == 200
    first_updated_at = first_response.json()["updated_at"]

    second_response = api_client.put(url, data=second_payload, format="json")
    assert second_response.status_code == 200
    body = second_response.json()

    assert body["draft"] == second_payload
    assert body["updated_at"] is not None
    assert body["updated_at"] >= first_updated_at

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == second_payload
