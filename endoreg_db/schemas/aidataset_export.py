from __future__ import annotations

from typing import Any, Literal, Mapping

from lx_dtypes.models.contracts.aidataset_export import AIDataSetExportSummary
from pydantic import BaseModel, ConfigDict, Field, field_validator


AI_DATASET_EXPORT_REQUEST_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class AIDataSetExportRequestPayload(BaseModel):
    """Canonical request persisted for one EndoReg AI dataset export."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["1.0"] = AI_DATASET_EXPORT_REQUEST_SCHEMA_VERSION
    dataset_id: int | None = Field(default=None, ge=1)
    ai_dataset_name: str | None = None
    ai_dataset_type: Literal["image", "video"] | None = None
    center_key: str | None = None
    all_centers: bool = False
    only_validated: bool = True

    @field_validator("dataset_id", mode="before")
    @classmethod
    def _normalize_dataset_id(cls, value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            raise ValueError("dataset_id must be an integer")
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError as exc:
                raise ValueError("dataset_id must be an integer") from exc
        return value

    @field_validator("all_centers", "only_validated", mode="before")
    @classmethod
    def _normalize_boolean(cls, value: Any) -> Any:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return value

    @field_validator("ai_dataset_name", "center_key", mode="after")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value or None


def parse_ai_dataset_export_request_payload(
    value: Mapping[str, Any] | AIDataSetExportRequestPayload | None,
) -> AIDataSetExportRequestPayload:
    if isinstance(value, AIDataSetExportRequestPayload):
        return value
    return AIDataSetExportRequestPayload.model_validate(dict(value or {}))


def dump_ai_dataset_export_request_payload(
    value: Mapping[str, Any] | AIDataSetExportRequestPayload | None,
) -> dict[str, Any]:
    return parse_ai_dataset_export_request_payload(value).model_dump(
        mode="json",
        exclude_none=True,
    )


def dump_ai_dataset_export_summary(value: Any) -> dict[str, int]:
    summary = (
        value
        if isinstance(value, AIDataSetExportSummary)
        else AIDataSetExportSummary.model_validate(value)
    )
    return summary.model_dump(mode="json")


__all__ = [
    "AI_DATASET_EXPORT_REQUEST_SCHEMA_VERSION",
    "AIDataSetExportRequestPayload",
    "dump_ai_dataset_export_request_payload",
    "dump_ai_dataset_export_summary",
    "parse_ai_dataset_export_request_payload",
]
