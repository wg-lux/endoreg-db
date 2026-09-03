from __future__ import annotations

from typing import Any, cast

# pyright: reportUnknownMemberType=false

import pytest
from django.utils import timezone

from endoreg_db.models import (
    Examination,
    ExaminationIndication,
    ExaminationIndicationClassification,
    ExaminationIndicationClassificationChoice,
    Patient,
    PatientExamination,
    PatientExaminationIndication,
    PatientExaminationReport,
)
from endoreg_db.services.report_persistence import (
    ReportPersistenceValidationError,
    save_report_submission,
)
from endoreg_db.services.report_pdf_renderer import build_report_template_pdf_payload
from endoreg_db.services.report_runtime_validation import ReportRuntimeValidationError


def _successful_runtime_validation(
    *_args: object,
    **_kwargs: object,
) -> dict[str, object]:
    return {"ok": True, "issues": []}


@pytest.mark.django_db
def test_save_report_submission_final_purges_patient_examination_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="report-purge-patient",
        first_name="Report",
        last_name="Purge",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination.report_draft = {
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
        "payload": {"sections": [{"id": "findings"}]},
    }
    patient_examination.draft_updated_at = timezone.now()
    patient_examination.save(update_fields=["report_draft", "draft_updated_at"])

    def successful_materialization(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[int, int]:
        return 41, 42

    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.persist_report_pdf_artifact",
        successful_materialization,
    )
    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.validate_final_report_submission",
        _successful_runtime_validation,
    )

    result = save_report_submission(
        patient_examination_id=patient_examination.pk,
        template_name="star_upper_gi_main",
        editor_payload={"sections": [{"id": "findings"}]},
        rendered_text="Final report text",
        status=PatientExaminationReport.Status.FINAL,
    )

    assert result.report.status == PatientExaminationReport.Status.FINAL
    assert result.report.editor_payload == {
        "sections": [{"id": "findings"}],
        "report_language": "de",
    }
    assert result.persisted_report_artifact_id == 41
    assert result.persisted_pdf_artifact_id == 42

    patient_examination.refresh_from_db()
    assert patient_examination.report_draft == {}
    assert patient_examination.draft_updated_at is None


@pytest.mark.django_db
def test_final_submission_rolls_back_when_pdf_materialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="report-materialization-rollback",
        first_name="Report",
        last_name="Rollback",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination.report_draft = {
        "module_name": "report_template_examples",
        "template_name": "star_upper_gi_main",
    }
    patient_examination.save(update_fields=["report_draft"])

    def fail_materialization(*_args: object, **_kwargs: object) -> tuple[int, int]:
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.persist_report_pdf_artifact",
        fail_materialization,
    )
    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.validate_final_report_submission",
        _successful_runtime_validation,
    )

    with pytest.raises(RuntimeError, match="renderer unavailable"):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="star_upper_gi_main",
            rendered_text="Deutscher Befundtext",
            status=PatientExaminationReport.Status.FINAL,
        )

    assert PatientExaminationReport.objects.count() == 0
    patient_examination.refresh_from_db()
    assert patient_examination.report_draft["template_name"] == "star_upper_gi_main"


@pytest.mark.django_db
def test_final_submission_rolls_back_when_template_requirements_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient = Patient.objects.create(
        patient_hash="report-runtime-rollback",
        first_name="Before",
        last_name="Validation",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)

    def fail_runtime_validation(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ReportRuntimeValidationError(
            {
                "ok": False,
                "issues": [
                    {"code": "required_finding_missing", "message": "Befund fehlt."}
                ],
            }
        )

    monkeypatch.setattr(
        "endoreg_db.services.report_persistence.validate_final_report_submission",
        fail_runtime_validation,
    )

    with pytest.raises(ReportRuntimeValidationError):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="star_upper_gi_main",
            status=PatientExaminationReport.Status.FINAL,
            patient_data={"first_name": "Must Roll Back"},
        )

    patient.refresh_from_db()
    assert patient.first_name == "Before"
    assert PatientExaminationReport.objects.count() == 0


def _create_indication_graph() -> tuple[
    PatientExamination,
    ExaminationIndication,
    ExaminationIndicationClassificationChoice,
]:
    patient = Patient.objects.create(
        patient_hash="report-indication-patient",
        first_name="Report",
        last_name="Indication",
    )
    examination = Examination.objects.create(name="report-indication-examination")
    indication = ExaminationIndication.objects.create(name="report-indication")
    classification = ExaminationIndicationClassification.objects.create(
        name="report-indication-classification"
    )
    choice = ExaminationIndicationClassificationChoice.objects.create(
        name="report-indication-choice"
    )
    classification.choices.add(choice)
    indication.classifications.add(classification)
    examination.indications.add(indication)
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        examination=examination,
    )
    return patient_examination, indication, choice


@pytest.mark.django_db
def test_report_submission_validates_indications_before_replacing_rows() -> None:
    patient_examination, indication, choice = _create_indication_graph()
    existing = PatientExaminationIndication.objects.create(
        patient_examination=patient_examination,
        examination_indication=indication,
        indication_choice=choice,
    )

    with pytest.raises(
        ReportPersistenceValidationError,
        match="Unknown examination indication",
    ):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="indication-report",
            indications=[{"examination_indication_id": 999999}],
        )

    assert PatientExaminationIndication.objects.get().pk == existing.pk
    assert PatientExaminationReport.objects.count() == 0


@pytest.mark.django_db
def test_report_submission_rejects_choice_from_another_indication() -> None:
    patient_examination, _indication, choice = _create_indication_graph()
    other_indication = ExaminationIndication.objects.create(
        name="report-other-indication"
    )
    examination = patient_examination.examination
    assert examination is not None
    examination.indications.add(other_indication)

    with pytest.raises(
        ReportPersistenceValidationError,
        match="not allowed for this examination indication",
    ):
        save_report_submission(
            patient_examination_id=patient_examination.pk,
            template_name="indication-report",
            indications=[
                {
                    "examination_indication_id": other_indication.pk,
                    "indication_choice_id": choice.pk,
                }
            ],
        )

    assert PatientExaminationIndication.objects.count() == 0
    assert PatientExaminationReport.objects.count() == 0


@pytest.mark.django_db
def test_report_submission_persists_valid_indication_selection() -> None:
    patient_examination, indication, choice = _create_indication_graph()

    result = save_report_submission(
        patient_examination_id=patient_examination.pk,
        template_name="indication-report",
        indications=[
            {
                "examination_indication_id": indication.pk,
                "indication_choice_id": choice.pk,
            }
        ],
    )

    persisted = PatientExaminationIndication.objects.get()
    assert cast(Any, persisted).examination_indication_id == indication.pk
    assert cast(Any, persisted).indication_choice_id == choice.pk
    assert result.report.editor_payload == {"report_language": "de"}


@pytest.mark.django_db
def test_pdf_payload_uses_german_fallback_copy_from_persisted_language() -> None:
    patient = Patient.objects.create(
        patient_hash="report-german-pdf",
        first_name="Erika",
        last_name="Muster",
    )
    patient_examination = PatientExamination.objects.create(patient=patient)
    report = PatientExaminationReport.objects.create(
        patient_examination=patient_examination,
        template_name="gastroskopie",
        editor_payload={"report_language": "de"},
    )

    payload = build_report_template_pdf_payload(
        report=report,
        patient_examination=patient_examination,
        frame_image_paths=["/encrypted-storage/frame-1.jpg"],
    )

    assert payload["title"] == "gastroskopie Befundbericht"
    assert payload["blocks"][0]["section_title"] == "Befund"
    assert payload["blocks"][0]["sentences"][0]["variables"]["text"] == (
        "Kein Befundtext verfügbar."
    )
    assert payload["blocks"][1]["title"] == "Bilddokumentation"
    assert payload["blocks"][1]["captions"] == ["Bild 1"]


@pytest.mark.django_db
def test_report_submission_persists_first_class_language_and_kb_provenance() -> None:
    patient = Patient.objects.create(
        patient_hash="report-first-class-provenance",
        first_name="Erika",
        last_name="Muster",
    )
    patient_examination = PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module="report_template_examples",
        knowledge_base_version="0.1.0",
    )

    result = save_report_submission(
        patient_examination_id=patient_examination.pk,
        template_name="star_upper_gi_main",
        editor_payload={"reportLanguage": "de"},
    )

    assert result.report.language == "de"
    assert result.report.knowledge_base_module == "report_template_examples"
    assert result.report.knowledge_base_version == "0.1.0"
    assert result.report.editor_payload == {"report_language": "de"}
