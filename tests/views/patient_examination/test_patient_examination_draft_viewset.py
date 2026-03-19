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
