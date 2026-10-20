from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from lx_dtypes.models.contracts.patient_examination_report import ReportJsonObject

from endoreg_db.models import Patient, PatientExamination, PatientExaminationReport


def _patient_examination() -> PatientExamination:
    patient = Patient.objects.create(
        patient_hash=f"report-json-{uuid4().hex}",
        first_name="Report",
        last_name="Validation",
    )
    return PatientExamination.objects.create(patient=patient)


@pytest.mark.django_db
def test_patient_examination_report_canonicalizes_json_fields_on_direct_save() -> None:
    report = PatientExaminationReport.objects.create(
        patient_examination=_patient_examination(),
        template_name="standard-report",
        editor_payload={"sections": [{"id": "findings", "enabled": True}]},
        patient_context_snapshot={"patient_birth_date": "1970-01-02"},
        history_context_snapshot={
            "history_depth": 5,
            "previous_examinations": [],
        },
    )

    report.refresh_from_db()
    assert report.editor_payload == {"sections": [{"id": "findings", "enabled": True}]}
    assert report.patient_context_snapshot == {"patient_birth_date": "1970-01-02"}
    assert report.history_context_snapshot == {
        "history_depth": 5,
        "previous_examinations": [],
    }


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("editor_payload", ["not", "an", "object"]),
        ("patient_context_snapshot", {"captured_at": datetime.now()}),
        ("history_context_snapshot", {"history_depth": float("nan")}),
        ("runtime_validation_snapshot", {"ok": True}),
        ("runtime_validation_snapshot", {"unvalidated": "evaluation"}),
        ("dtypes_record", {"unknown_clinical_field": "invalid"}),
        ("dtypes_record", ["invalid"]),
    ],
)
def test_patient_examination_report_rejects_invalid_direct_json_writes(
    field_name: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "patient_examination": _patient_examination(),
        "template_name": "standard-report",
        field_name: value,
    }

    with pytest.raises(ValidationError) as exc_info:
        PatientExaminationReport.objects.create(**kwargs)

    assert field_name in exc_info.value.message_dict


@pytest.mark.django_db
def test_report_runtime_evaluation_roundtrip_and_template_identity() -> None:
    result: ReportJsonObject = {
        "template_name": "standard-report",
        "ok": True,
        "evaluated_findings_count": 0,
        "classification_validators": [],
        "intervention_validators": [],
        "findings_validators": [],
        "examination_validators": [],
        "unit_validators": [],
        "issues": [],
    }
    report = PatientExaminationReport.objects.create(
        patient_examination=_patient_examination(),
        template_name="standard-report",
        runtime_validation_snapshot=result,
    )
    report.refresh_from_db()
    assert report.runtime_validation_snapshot == result
    report.template_name = "different-report"
    with pytest.raises(ValidationError, match="template"):
        report.save()
    report.template_name = "standard-report"
    report.runtime_validation_snapshot = {**result, "unknown_result": True}
    with pytest.raises(ValidationError, match="runtime_validation_snapshot"):
        report.save()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("template_name", "  "),
        ("status", "translated_final"),
        ("language", "fr"),
        ("version", 0),
        ("version", True),
    ],
)
def test_patient_examination_report_rejects_invalid_direct_lifecycle_writes(
    field_name: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "patient_examination": _patient_examination(),
        "template_name": "standard-report",
        field_name: value,
    }

    with pytest.raises(ValidationError) as exc_info:
        PatientExaminationReport.objects.create(**kwargs)

    assert field_name in exc_info.value.message_dict


@pytest.mark.django_db
def test_patient_examination_report_canonicalizes_language_provenance() -> None:
    report = PatientExaminationReport.objects.create(
        patient_examination=_patient_examination(),
        template_name="standard-report",
        editor_payload={"reportLanguage": "de"},
    )

    report.refresh_from_db()
    assert report.editor_payload == {"report_language": "de"}


@pytest.mark.django_db
def test_patient_examination_report_rejects_unsupported_language() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PatientExaminationReport.objects.create(
            patient_examination=_patient_examination(),
            template_name="standard-report",
            editor_payload={"report_language": "fr"},
        )

    assert "editor_payload" in exc_info.value.message_dict


@pytest.mark.django_db
def test_patient_examination_report_requires_complete_knowledge_base_identity() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PatientExaminationReport.objects.create(
            patient_examination=_patient_examination(),
            template_name="standard-report",
            knowledge_base_module="report_template_examples",
        )

    assert "knowledge_base_module" in exc_info.value.message_dict
