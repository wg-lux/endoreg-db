from __future__ import annotations

# pyright: reportUnknownMemberType=false

import math
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


def _successful_final_runtime_validation(
    patient_examination: PatientExamination,
    *,
    template_name: str,
) -> dict[str, object]:
    del patient_examination, template_name
    return {"ok": True, "issues": []}


def _successful_final_artifact_persistence(
    report: PatientExaminationReport,
    patient_examination: PatientExamination,
    *,
    rendered_text: str,
    strict_renderer: bool = False,
) -> tuple[None, None]:
    del report, patient_examination, rendered_text, strict_renderer
    return None, None


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
    assert body["revision"] == 0
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
        "indications": [{"examination_indication_id": 12, "indication_choice_id": 21}],
        "template_section_drafts": {
            "findings": {
                "note": "No acute bleeding",
                "include_patient_data": True,
                "include_examination_data": False,
            }
        },
        "selected_report_language": "de",
        "active_report_id": 88,
        "report_text_mode": "manual",
        "rendered_text": "Klinischer Freitext",
        "payload": {
            "sections": [
                {"name": "findings", "items": ["esophagus_polyp"]},
            ]
        },
    }
    canonical_payload = {
        "schema_version": "1.0",
        "revision": 1,
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "template_identity": {
            "module_name": "report_template_examples",
            "knowledge_base_version": "0.2.8",
            "template_version": "3",
            "template_hash": "sha256:template",
            "lifecycle_status": "published",
        },
        "indications": [{"examination_indication_id": 12, "indication_choice_id": 21}],
        "template_section_drafts": {
            "findings": {
                "note": "No acute bleeding",
                "include_patient_data": True,
                "include_examination_data": False,
            }
        },
        "selected_report_language": "de",
        "active_report_id": 88,
        "report_text_mode": "manual",
        "rendered_text": "Klinischer Freitext",
        "payload": {
            "sections": [
                {"name": "findings", "items": ["esophagus_polyp"]},
            ]
        },
    }

    response = api_client.put(
        url, data={**payload, "expected_revision": 0}, format="json"
    )
    assert response.status_code == 200

    body = _response_body(response)
    assert body["patient_examination_id"] == patient_examination_id
    assert body["revision"] == 1
    assert body["draft"] == canonical_payload
    assert body["updated_at"] is not None

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == canonical_payload
    assert patient_examination.draft_updated_at is not None

    response = api_client.get(url)
    assert response.status_code == 200
    assert _response_body(response)["revision"] == 1
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

    first_response = api_client.put(
        url, data={**first_payload, "expected_revision": 0}, format="json"
    )
    assert first_response.status_code == 200

    first_body = _response_body(first_response)
    first_updated_at = cast(str, first_body["updated_at"])

    second_response = api_client.put(
        url, data={**second_payload, "expected_revision": 1}, format="json"
    )
    assert second_response.status_code == 200

    second_body = _response_body(second_response)
    second_updated_at = cast(str, second_body["updated_at"])

    assert second_body["draft"] == {
        "schema_version": "1.0",
        "revision": 2,
        "indications": [],
        "template_section_drafts": {},
        "selected_report_language": "de",
        "report_text_mode": "generated",
        "rendered_text": "",
        **second_payload,
    }
    assert second_updated_at >= first_updated_at

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {
        "schema_version": "1.0",
        "revision": 2,
        "indications": [],
        "template_section_drafts": {},
        "selected_report_language": "de",
        "report_text_mode": "generated",
        "rendered_text": "",
        **second_payload,
    }


@pytest.mark.django_db
def test_patient_examination_draft_rejects_stale_second_writer(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-conflict-{uuid4().hex}",
        first_name="Draft",
        last_name="Conflict",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    url = f"/api/patient-examinations/{_pk(patient_examination)}/draft/"

    first_writer = api_client.put(
        url,
        data={
            "expected_revision": 0,
            "module_name": "report_template_examples",
            "template_name": "template_a",
            "rendered_text": "First writer",
        },
        format="json",
    )
    stale_writer = api_client.put(
        url,
        data={
            "expected_revision": 0,
            "module_name": "report_template_examples",
            "template_name": "template_a",
            "rendered_text": "Stale writer",
        },
        format="json",
    )

    assert first_writer.status_code == 200
    assert _response_body(first_writer)["revision"] == 1
    assert stale_writer.status_code == 409
    stale_body = _response_body(stale_writer)
    assert stale_body["current_revision"] == 1
    assert stale_body["updated_at"] is not None

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft["revision"] == 1
    assert patient_examination.report_draft["rendered_text"] == "First writer"


@pytest.mark.django_db
def test_patient_examination_draft_requires_expected_revision(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-revision-required-{uuid4().hex}",
        first_name="Draft",
        last_name="Revision",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    response = api_client.put(
        f"/api/patient-examinations/{_pk(patient_examination)}/draft/",
        data={"module_name": "report_template_examples"},
        format="json",
    )

    assert response.status_code == 400
    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}


@pytest.mark.django_db
def test_patient_examination_draft_upgrades_legacy_unrevisioned_draft(
    api_client: APIClient,
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-legacy-revision-{uuid4().hex}",
        first_name="Draft",
        last_name="Legacy",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    PatientExamination.objects.filter(pk=patient_examination.pk).update(
        report_draft={
            "schema_version": "1.0",
            "module_name": "report_template_examples",
            "template_name": "legacy_template",
            "payload": {},
        }
    )
    url = f"/api/patient-examinations/{_pk(patient_examination)}/draft/"

    legacy_response = api_client.get(url)
    update_response = api_client.put(
        url,
        data={
            "expected_revision": 0,
            "module_name": "report_template_examples",
            "template_name": "legacy_template",
            "rendered_text": "Updated legacy draft",
        },
        format="json",
    )

    assert legacy_response.status_code == 200
    assert _response_body(legacy_response)["revision"] == 0
    assert update_response.status_code == 200
    assert _response_body(update_response)["revision"] == 1
    patient_examination.refresh_from_db()
    assert patient_examination.report_draft["revision"] == 1


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
        data={
            "module_name": "reports",
            "expected_revision": 0,
            "unexpected": "value",
        },
        format="json",
    )

    assert response.status_code == 400
    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "invalid_draft",
    [
        {"schema_version": "2.0", "payload": {}},
        {"module_name": 7, "payload": {}},
        {"payload": []},
        {"payload": {"measurement": math.nan}},
        {"payload": {"measurement": math.inf}},
    ],
)
def test_patient_examination_rejects_invalid_direct_draft_write(
    invalid_draft: dict[str, object],
) -> None:
    patient = Patient.objects.create(
        patient_hash=f"draft-direct-{uuid4().hex}",
        first_name="Draft",
        last_name="Direct",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination.report_draft = invalid_draft

    with pytest.raises(DjangoValidationError) as exc_info:
        patient_examination.save(update_fields=["report_draft"])

    assert "report_draft" in exc_info.value.message_dict

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}


@pytest.mark.django_db
def test_patient_examination_draft_is_empty_after_final_report_save(
    api_client: APIClient,
    monkeypatch: pytest.MonkeyPatch,
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

    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.validate_final_report_submission",
        _successful_final_runtime_validation,
    )
    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.persist_report_pdf_artifact",
        _successful_final_artifact_persistence,
    )

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
