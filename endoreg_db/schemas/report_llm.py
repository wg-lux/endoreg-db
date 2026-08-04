from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from lx_dtypes.models.contracts.patient_examination_report import ReportJsonObject
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    TypeAdapter,
    field_validator,
)


REPORT_LLM_JOB_SCHEMA_VERSION: Literal["1.0"] = "1.0"
type ReportLlmOperation = Literal["report_llm_reimport", "report_llm_import"]

_JSON_OBJECT_ADAPTER = TypeAdapter(
    ReportJsonObject,
    config=ConfigDict(strict=True, allow_inf_nan=False),
)


class ReportLlmJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = REPORT_LLM_JOB_SCHEMA_VERSION
    kind: ReportLlmOperation
    queue: str = Field(min_length=1)
    retry: bool = True
    request_payload: ReportJsonObject = Field(default_factory=dict)


class ReportLlmReimportRequestPayload(BaseModel):
    """Canonical external request accepted by the report re-import endpoint."""

    model_config = ConfigDict(extra="forbid", strict=True)

    retry: StrictBool = True


class ReportLlmJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = REPORT_LLM_JOB_SCHEMA_VERSION
    upload_job_id: str | None = None
    pdf_id: int | None = Field(default=None, ge=1)
    pdf_hash: str | None = None
    sensitive_meta_created: bool | None = None
    sensitive_meta_id: int | None = Field(default=None, ge=1)
    text_extracted: bool | None = None
    anonymized: bool | None = None
    processed_file_sha256: str | None = None
    old_sensitive_meta_id: int | None = Field(default=None, ge=1)
    processing_upload_jobs: int | None = Field(default=None, ge=0)
    anonymized_upload_jobs: int | None = Field(default=None, ge=0)

    @field_validator("processed_file_sha256")
    @classmethod
    def _validate_processed_file_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef" for character in normalized
        ):
            raise ValueError(
                "processed_file_sha256 must be a 64-character SHA-256 hex digest"
            )
        return normalized


def build_report_llm_job_config(
    payload: Mapping[str, Any] | None,
    *,
    queue: str,
    operation: ReportLlmOperation,
) -> ReportLlmJobConfig:
    request_payload = _JSON_OBJECT_ADAPTER.validate_python(
        dict(payload or {}),
        strict=True,
    )
    retry_value = request_payload.get("retry")
    retry = (
        True
        if retry_value is None
        else str(retry_value).strip().lower() not in {"0", "false", "no"}
    )
    return ReportLlmJobConfig(
        kind=operation,
        queue=queue,
        retry=retry,
        request_payload=request_payload,
    )


def dump_report_llm_reimport_request_payload(
    value: ReportLlmReimportRequestPayload,
) -> dict[str, bool]:
    return value.model_dump(mode="json")


def dump_report_llm_job_config(
    value: Mapping[str, Any] | ReportLlmJobConfig,
) -> dict[str, Any]:
    config = (
        value
        if isinstance(value, ReportLlmJobConfig)
        else ReportLlmJobConfig.model_validate(dict(value))
    )
    return config.model_dump(mode="json")


def dump_report_llm_job_result(
    value: Mapping[str, Any] | ReportLlmJobResult | None,
) -> dict[str, Any]:
    if value is None or value == {}:
        return {}
    result = (
        value
        if isinstance(value, ReportLlmJobResult)
        else ReportLlmJobResult.model_validate(dict(value))
    )
    return result.model_dump(mode="json", exclude_none=True)


__all__ = [
    "REPORT_LLM_JOB_SCHEMA_VERSION",
    "ReportLlmJobConfig",
    "ReportLlmJobResult",
    "ReportLlmOperation",
    "ReportLlmReimportRequestPayload",
    "build_report_llm_job_config",
    "dump_report_llm_job_config",
    "dump_report_llm_job_result",
    "dump_report_llm_reimport_request_payload",
]
