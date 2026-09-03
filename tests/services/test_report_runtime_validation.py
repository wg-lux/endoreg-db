from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from endoreg_db.models import Patient, PatientExamination
from endoreg_db.services.report_runtime_validation import (
    ReportRuntimeValidationError,
    validate_final_report_submission,
)


class _KnowledgeBase:
    def __init__(self, result: Mapping[str, object]) -> None:
        self._result = result

    def evaluate_report_template_validators(
        self,
        name: str,
        *,
        p_examination: object,
    ) -> Mapping[str, object]:
        assert name == "star_upper_gi_main"
        assert getattr(p_examination, "examination") == "gastroscopy"
        return self._result


def _issue_code(result: Mapping[str, object]) -> str:
    issues = result.get("issues")
    assert isinstance(issues, list)
    typed_issues = cast(list[object], issues)
    assert typed_issues
    first_issue = typed_issues[0]
    assert isinstance(first_issue, Mapping)
    issue_mapping = cast(Mapping[object, object], first_issue)
    code = issue_mapping.get("code")
    assert isinstance(code, str)
    return code


def _patient_examination() -> PatientExamination:
    patient = Patient.objects.create(
        patient_hash="report-runtime-validation",
    )
    return PatientExamination.objects.create(
        patient=patient,
        knowledge_base_module="report_template_examples",
        knowledge_base_version="0.1.0",
        dtypes_record={
            "patient": str(patient.pk),
            "examination": "gastroscopy",
            "knowledge_base_module": "report_template_examples",
            "knowledge_base_version": "0.1.0",
        },
    )


@pytest.mark.django_db
def test_final_report_runtime_validation_returns_typed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load_successful_kb(*_args: object, **_kwargs: object) -> _KnowledgeBase:
        return _KnowledgeBase(
            {
                "template_name": "star_upper_gi_main",
                "ok": True,
                "evaluated_findings_count": 0,
                "issues": [],
            }
        )

    monkeypatch.setattr(
        "endoreg_db.services.report_runtime_validation.load_knowledge_base",
        load_successful_kb,
    )

    result = validate_final_report_submission(
        _patient_examination(),
        template_name="star_upper_gi_main",
    )

    assert result["ok"] is True
    assert result["template_name"] == "star_upper_gi_main"


@pytest.mark.django_db
def test_final_report_runtime_validation_rejects_failed_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load_failing_kb(*_args: object, **_kwargs: object) -> _KnowledgeBase:
        return _KnowledgeBase(
            {
                "template_name": "star_upper_gi_main",
                "ok": False,
                "evaluated_findings_count": 0,
                "issues": [
                    {"code": "required_finding_missing", "message": "Befund fehlt."}
                ],
            }
        )

    monkeypatch.setattr(
        "endoreg_db.services.report_runtime_validation.load_knowledge_base",
        load_failing_kb,
    )

    with pytest.raises(ReportRuntimeValidationError) as exc_info:
        validate_final_report_submission(
            _patient_examination(),
            template_name="star_upper_gi_main",
        )

    assert exc_info.value.result["ok"] is False
    assert _issue_code(exc_info.value.result) == "required_finding_missing"


@pytest.mark.django_db
def test_final_report_runtime_validation_rejects_missing_kb_identity() -> None:
    patient = Patient.objects.create(patient_hash="report-runtime-no-kb")
    patient_examination = PatientExamination.objects.create(patient=patient)
    patient_examination.knowledge_base_module = ""
    patient_examination.knowledge_base_version = ""

    with pytest.raises(ReportRuntimeValidationError) as exc_info:
        validate_final_report_submission(
            patient_examination,
            template_name="star_upper_gi_main",
        )

    assert _issue_code(exc_info.value.result) == "missing_knowledge_base_identity"
