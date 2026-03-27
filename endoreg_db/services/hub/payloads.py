from __future__ import annotations

from datetime import date, time
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class PreanonymizedIngestPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    external_id: str | None = None
    external_id_origin: str | None = None
    casenumber: str | None = None

    patient_first_name: str | None = None
    patient_last_name: str | None = None
    patient_dob: date | None = None
    patient_gender: str | None = None

    examination_date: date | None = None
    examination_time: time | None = None

    anonymized_text: str | None = None
    text: str | None = None
    file_path: str | None = None
    endoscope_type: str | None = None
    endoscope_sn: str | None = None

    patient_hash: str | None = None
    examination_hash: str | None = None

    center_key: str | None = None
    center_name: str | None = None

    source_system: str | None = None
    source_document_type: str | None = None
    original_document_id: str | None = None
    original_document_version: str | None = None
    raw_columns: dict[str, Any] | None = None

    @field_validator(
        "external_id",
        "external_id_origin",
        "casenumber",
        "patient_first_name",
        "patient_last_name",
        "patient_gender",
        "anonymized_text",
        "text",
        "file_path",
        "endoscope_type",
        "endoscope_sn",
        "patient_hash",
        "examination_hash",
        "center_key",
        "center_name",
        "source_system",
        "source_document_type",
        "original_document_id",
        "original_document_version",
        mode="before",
    )
    @classmethod
    def _normalize_blank_strings(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


__all__ = ["PreanonymizedIngestPayload"]
