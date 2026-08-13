from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from lx_dtypes.models.contracts.dtypes_record_persistence import (
    parse_dtypes_record_persistence_payload,
)
from lx_dtypes.models.contracts.patient_examination_report import (
    ReportJsonObject,
    report_json_safe_dict,
)
from lx_dtypes.models.interface.KnowledgeBaseResolver import load_knowledge_base
from lx_dtypes.models.ledger.p_examination.Pydantic import PExamination
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.models.medical.patient.patient_examination import PatientExamination


class _KnowledgeBaseRuntime(Protocol):
    def evaluate_report_template_validators(
        self,
        name: str,
        p_examination: PExamination,
    ) -> Mapping[str, object]: ...


class ReportRuntimeValidationError(ValueError):
    def __init__(self, result: ReportJsonObject) -> None:
        super().__init__("Report template runtime validation failed.")
        self.result = result


def validate_final_report_submission(
    patient_examination: PatientExamination,
    *,
    template_name: str,
) -> ReportJsonObject:
    """Run the published template validators against the persisted ledger snapshot."""
    module_name = patient_examination.knowledge_base_module.strip()
    version = patient_examination.knowledge_base_version.strip()
    if not module_name or not version:
        raise ReportRuntimeValidationError(
            {
                "ok": False,
                "issues": [
                    {
                        "code": "missing_knowledge_base_identity",
                        "message": (
                            "Final reports require knowledge_base_module and "
                            "knowledge_base_version."
                        ),
                    }
                ],
            }
        )

    try:
        persisted = parse_dtypes_record_persistence_payload(
            patient_examination.dtypes_record
        )
    except PydanticValidationError as exc:
        raise ReportRuntimeValidationError(
            {
                "ok": False,
                "issues": [
                    {
                        "code": "invalid_persisted_report_ledger",
                        "message": "The persisted report ledger is not valid.",
                    }
                ],
            }
        ) from exc
    if (
        persisted.knowledge_base_module != module_name
        or persisted.knowledge_base_version != version
    ):
        raise ReportRuntimeValidationError(
            {
                "ok": False,
                "issues": [
                    {
                        "code": "knowledge_base_identity_mismatch",
                        "message": (
                            "Persisted report ledger and PatientExamination use "
                            "different knowledge-base identities."
                        ),
                    }
                ],
            }
        )

    payload = PExamination.model_validate(
        persisted.model_dump(mode="python", exclude_none=True)
    )
    try:
        knowledge_base = cast(
            _KnowledgeBaseRuntime,
            load_knowledge_base(module_name, version=version),
        )
        result = report_json_safe_dict(
            knowledge_base.evaluate_report_template_validators(
                template_name,
                p_examination=payload,
            )
        )
    except (KeyError, ValueError) as exc:
        raise ReportRuntimeValidationError(
            {
                "ok": False,
                "issues": [
                    {
                        "code": "report_template_validation_unavailable",
                        "message": (
                            "The versioned report template could not be validated."
                        ),
                    }
                ],
            }
        ) from exc
    if result.get("ok") is not True:
        raise ReportRuntimeValidationError(result)
    return result


__all__ = [
    "ReportRuntimeValidationError",
    "validate_final_report_submission",
]
