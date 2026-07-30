from __future__ import annotations

# pyright: reportUnknownMemberType=false

from typing import Any, cast
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from endoreg_db.models import Patient, PatientExamination, PatientExaminationReport
from endoreg_db.services.report_persistence import save_report_submission


def _response_body(response: Any) -> dict[str, object]:
    return cast(dict[str, object], response.data)


def _pk(instance: object) -> int:
    return int(cast(Any, instance).pk)


@pytest.mark.django_db
def test_patient_examination_draft_roundtrip(api_client: APIClient) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-patient-{uuid4().hex}",
        first_name="Draft",
        last_name="Patient",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination_id = _pk(patient_examination)

    url = f"/api/patient-examinations/{patient_examination_id}/draft/"

    response = api_client.get(url)
    assert response.status_code == 200

    body = _response_body(response)
    assert body["patient_examination_id"] == patient_examination_id
    assert body["draft"] == {}
    assert body["updated_at"] is None

    payload = {
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "template_identity": {
            "moduleName": "report_template_examples",
            "knowledgeBaseVersion": "0.2.8",
            "templateVersion": "3",
            "templateHash": "sha256:template",
            "lifecycleStatus": "published",
        },
        "payload": {
            "sections": [
                {"name": "findings", "items": ["esophagus_polyp"]},
            ]
        },
    }
    canonical_payload = {
        "schema_version": "1.0",
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "template_identity": {
            "module_name": "report_template_examples",
            "knowledge_base_version": "0.2.8",
            "template_version": "3",
            "template_hash": "sha256:template",
            "lifecycle_status": "published",
        },
        "payload": {
            "sections": [
                {"name": "findings", "items": ["esophagus_polyp"]},
            ]
        },
    }

    response = api_client.put(url, data=payload, format="json")
    assert response.status_code == 200

    body = _response_body(response)
    assert body["patient_examination_id"] == patient_examination_id
    assert body["draft"] == canonical_payload
    assert body["updated_at"] is not None

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == canonical_payload
    assert patient_examination.draft_updated_at is not None

    response = api_client.get(url)
    assert response.status_code == 200
    assert _response_body(response)["draft"] == canonical_payload


@pytest.mark.django_db
def test_patient_examination_draft_returns_404_for_missing_exam(
    api_client: APIClient,
) -> None:
    response = api_client.get("/api/patient-examinations/999999/draft/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_patient_examination_draft_rejects_non_object_payload(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-invalid-{uuid4().hex}",
        first_name="Draft",
        last_name="Invalid",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination_id = _pk(patient_examination)

    response = api_client.put(
        f"/api/patient-examinations/{patient_examination_id}/draft/",
        data=["not", "an", "object"],
        format="json",
    )

    assert response.status_code == 400

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}
    assert patient_examination.draft_updated_at is None


@pytest.mark.django_db
def test_patient_examination_draft_put_overwrites_previous_payload(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-overwrite-{uuid4().hex}",
        first_name="Draft",
        last_name="Overwrite",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination_id = _pk(patient_examination)

    url = f"/api/patient-examinations/{patient_examination_id}/draft/"

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

    first_body = _response_body(first_response)
    first_updated_at = cast(str, first_body["updated_at"])

    second_response = api_client.put(url, data=second_payload, format="json")
    assert second_response.status_code == 200

    second_body = _response_body(second_response)
    second_updated_at = cast(str, second_body["updated_at"])

    assert second_body["draft"] == {
        "schema_version": "1.0",
        **second_payload,
    }
    assert second_updated_at >= first_updated_at

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {
        "schema_version": "1.0",
        **second_payload,
    }


@pytest.mark.django_db
def test_patient_examination_draft_rejects_unknown_fields(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-unknown-{uuid4().hex}",
        first_name="Draft",
        last_name="Unknown",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    response = api_client.put(
        f"/api/patient-examinations/{_pk(patient_examination)}/draft/",
        data={"module_name": "reports", "unexpected": "value"},
        format="json",
    )

    assert response.status_code == 400
    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}


@pytest.mark.django_db
def test_patient_examination_rejects_invalid_direct_draft_write() -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-direct-{uuid4().hex}",
        first_name="Draft",
        last_name="Direct",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination.report_draft = {
        "schema_version": "2.0",
        "payload": {},
    }

    with pytest.raises(DjangoValidationError) as exc_info:
        patient_examination.save(update_fields=["report_draft"])

    assert "report_draft" in exc_info.value.message_dict


@pytest.mark.django_db
def test_patient_examination_draft_is_empty_after_final_report_save(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-purge-{uuid4().hex}",
        first_name="Draft",
        last_name="Purge",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination_id = _pk(patient_examination)

    patient_examination.report_draft = {
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "payload": {"sections": [{"id": "findings"}]},
    }
    patient_examination.draft_updated_at = timezone.now()
    patient_examination.save(update_fields=["report_draft", "draft_updated_at"])

    save_report_submission(
        patient_examination_id=patient_examination_id,
        template_name="star_upper_gi_main",
        editor_payload={"sections": [{"id": "findings"}]},
        rendered_text="Final report text",
        status=PatientExaminationReport.Status.FINAL,
    )

    response = api_client.get(
        f"/api/patient-examinations/{patient_examination_id}/draft/"
    )

    assert response.status_code == 200

    body = _response_body(response)
    assert body["patient_examination_id"] == patient_examination_id
    assert body["draft"] == {}
    assert body["updated_at"] is None
