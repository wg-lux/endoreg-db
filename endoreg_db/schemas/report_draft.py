from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from lx_dtypes.models.contracts.json_types import JsonObject
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


REPORT_DRAFT_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class ReportDraftTemplateIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    module_name: str = Field(
        default="",
        validation_alias=AliasChoices("module_name", "moduleName"),
    )
    knowledge_base_version: str = Field(
        default="",
        validation_alias=AliasChoices(
            "knowledge_base_version",
            "knowledgeBaseVersion",
        ),
    )
    template_version: str = Field(
        default="",
        validation_alias=AliasChoices("template_version", "templateVersion"),
    )
    template_hash: str = Field(
        default="",
        validation_alias=AliasChoices("template_hash", "templateHash"),
    )
    lifecycle_status: str = Field(
        default="",
        validation_alias=AliasChoices("lifecycle_status", "lifecycleStatus"),
    )


class PatientExaminationReportDraft(BaseModel):
    """Versioned EndoReg editor state persisted on PatientExamination."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = REPORT_DRAFT_SCHEMA_VERSION
    module_name: str = ""
    template_name: str = ""
    template_identity: ReportDraftTemplateIdentity | None = None
    payload: JsonObject = Field(default_factory=dict)


def dump_patient_examination_report_draft(
    value: Mapping[str, Any] | PatientExaminationReportDraft | None,
) -> dict[str, Any]:
    if value is None or value == {}:
        return {}
    draft = (
        value
        if isinstance(value, PatientExaminationReportDraft)
        else PatientExaminationReportDraft.model_validate(dict(value))
    )
    return draft.model_dump(mode="json", exclude_none=True)


__all__ = [
    "PatientExaminationReportDraft",
    "REPORT_DRAFT_SCHEMA_VERSION",
    "ReportDraftTemplateIdentity",
    "dump_patient_examination_report_draft",
]
