"""Canonical empty successful evaluation used by persistence boundary tests."""

from lx_dtypes.models.knowledge_base.validators.ValidatorRuntimeDataDict import (
    ReportTemplateRuntimeValidationResultDataDict,
)


def successful_runtime_validation(
    *_args: object, template_name: str, **_kwargs: object
) -> ReportTemplateRuntimeValidationResultDataDict:
    return {
        "template_name": template_name,
        "ok": True,
        "evaluated_findings_count": 0,
        "classification_validators": [],
        "intervention_validators": [],
        "findings_validators": [],
        "examination_validators": [],
        "unit_validators": [],
        "issues": [],
    }
