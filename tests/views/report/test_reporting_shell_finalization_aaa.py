from __future__ import annotations

import json
from typing import cast
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.test import Client

from endoreg_db.models import Patient, PatientExamination, PatientExaminationReport


API_URL = "/api/patient-examination-reports/make-report"


def _payload(
    patient_examination: PatientExamination,
    report: PatientExaminationReport,
    *,
    module_name: str = "clinical_reporting",
    module_version: str = "2.0.0",
) -> dict[str, object]:
    return {
        "patient_examination_id": patient_examination.pk,
        "report_id": report.pk,
        "knowledge_base_module": module_name,
        "knowledge_base_version": module_version,
        "patient": {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "dob": "1815-12-10",
        },
    }


def _reporting_context() -> tuple[PatientExamination, PatientExaminationReport]:
    patient = Patient.objects.create(
        patient_hash=f"reporting-shell-final-{uuid4().hex}",
        first_name="Report",
        last_name="Patient",
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module="clinical_reporting",
        knowledge_base_version="2.0.0",
        report_draft={
            "schema_version": "1.0",
            "revision": 3,
            "module_name": "clinical_reporting",
            "template_name": "colonoscopy",
            "payload": {},
        },
    )
    report = PatientExaminationReport.objects.create(
        patient_examination=patient_examination,
        template_name="colonoscopy",
        knowledge_base_module="clinical_reporting",
        knowledge_base_version="2.0.0",
        status=PatientExaminationReport.Status.DRAFT,
        rendered_text="Normalbefund.",
    )
    return patient_examination, report


@pytest.fixture
def staff_client(db: object) -> Client:
    del db
    user = User.objects.create_user(
        username=f"reporting-shell-aaa-{uuid4().hex}",
        password="pw",
        is_staff=True,
    )
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_make_report_arranges_renderer_failure_and_asserts_finalization_rollback(
    staff_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    patient_examination, report = _reporting_context()
    original_draft = dict(patient_examination.report_draft)

    def successful_validation(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"ok": True, "issues": []}

    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report."
        "validate_final_report_submission",
        successful_validation,
    )
    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report."
        "persist_report_pdf_artifact",
        Mock(side_effect=RuntimeError("renderer unavailable")),
    )

    # Act
    response = staff_client.post(
        API_URL,
        data=json.dumps(_payload(patient_examination, report)),
        content_type="application/json",
    )

    # Assert
    assert response.status_code == 500
    assert "RuntimeError" in response.json()["detail"]
    report.refresh_from_db()
    patient_examination.refresh_from_db()
    assert report.status == PatientExaminationReport.Status.DRAFT
    assert report.finalized_at is None
    assert cast(object | None, getattr(report, "finalized_by_id", None)) is None
    assert report.runtime_validation_snapshot == {}
    assert patient_examination.report_draft == original_draft


@pytest.mark.django_db
def test_make_report_arranges_identity_mismatch_and_asserts_no_callee_or_mutation(
    staff_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    patient_examination, report = _reporting_context()
    validate = Mock(return_value={"ok": True, "issues": []})
    persist = Mock(return_value=(101, 202))
    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report."
        "validate_final_report_submission",
        validate,
    )
    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report."
        "persist_report_pdf_artifact",
        persist,
    )

    # Act
    response = staff_client.post(
        API_URL,
        data=json.dumps(
            _payload(
                patient_examination,
                report,
                module_version="3.0.0",
            )
        ),
        content_type="application/json",
    )

    # Assert
    assert response.status_code == 409
    assert "identity does not match" in response.json()["detail"]
    validate.assert_not_called()
    persist.assert_not_called()
    report.refresh_from_db()
    assert report.status == PatientExaminationReport.Status.DRAFT
    assert report.finalized_at is None


@pytest.mark.django_db
def test_make_report_arranges_no_frame_selections_and_asserts_empty_export(
    staff_client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    patient_examination, report = _reporting_context()
    validate = Mock(return_value={"ok": True, "issues": []})
    persist = Mock(return_value=(None, None))
    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report."
        "validate_final_report_submission",
        validate,
    )
    monkeypatch.setattr(
        "endoreg_db.views.report.patient_examination_report."
        "persist_report_pdf_artifact",
        persist,
    )

    # Act
    response = staff_client.post(
        API_URL,
        data=json.dumps(_payload(patient_examination, report)),
        content_type="application/json",
    )

    # Assert
    assert response.status_code == 200, response.content
    assert response.json()["included_frame_count"] == 0
    persist.assert_called_once()
    assert persist.call_args.kwargs["frame_image_paths"] == []
    assert persist.call_args.kwargs["frame_captions"] == []
    report.refresh_from_db()
    assert report.status == PatientExaminationReport.Status.FINAL
    assert report.finalized_at is not None
