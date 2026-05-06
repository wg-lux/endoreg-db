from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


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
    file_sha256: str | None = None
    human_anonymization_validated: bool | None = None
    validated_by: str | None = None
    validated_at: datetime | None = None
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
        "file_sha256",
        "validated_by",
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

    @field_validator("file_sha256")
    @classmethod
    def _validate_optional_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(
            ch not in "0123456789abcdef" for ch in normalized
        ):
            raise ValueError("file_sha256 must be a 64-character SHA-256 hex digest")
        return normalized


class LocalStudyServerPreanonymizedIngestPayload(PreanonymizedIngestPayload):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    center_key: str
    source_system: str
    file_sha256: str
    human_anonymization_validated: bool
    validated_by: str
    validated_at: datetime

    @field_validator("human_anonymization_validated")
    @classmethod
    def _require_human_validation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_anonymization_validated must be true")
        return value

    @field_validator("validated_at")
    @classmethod
    def _require_timezone_aware_validated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("validated_at must be timezone-aware ISO 8601")
        return value


class UploadProvenancePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entrypoint: str | None = None
    ingest_mode: str | None = None
    source_system: str | None = None
    content_hash: str | None = None
    source_center_key: str | None = None
    storage_class: str | None = None
    storage_tier: str | None = None
    retention_policy: str | None = None
    hub_mode: bool | None = None
    local_study_server: bool | None = None
    declared_center_key: str | None = None
    declared_center_name: str | None = None
    resolved_center_key: str | None = None
    watched_path: str | None = None
    file_type: str | None = None
    ingest_variant: str | None = None
    sidecar_path: str | None = None
    sidecar_payload: dict[str, Any] | None = None
    watcher_processing_path: str | None = None
    processor_name: str | None = None
    processing_handoff: str | None = None
    stored_upload_path: str | None = None
    quarantined_path: str | None = None
    quarantined_sidecar_path: str | None = None
    media_integrity_status: str | None = None
    media_integrity_reason: str | None = None
    media_integrity_missing_artifacts: list[str] | None = None
    previous_upload_job_id: str | None = None
    custom_marker: str | None = None
    legacy_source_path: str | None = None
    migrated_destination_path: str | None = None


class TransferMediaUploadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    media_role: str
    stored_name: str
    content_hash: str
    uploaded_name: str


class TransferCaseResolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: str
    created: bool
    reason: str
    linked_patient_examination_id: int | None = None
    linked_patient_id: int | None = None


class TransferProvenancePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    entrypoint: str | None = None
    source_node_key: str | None = None
    target_node_key: str | None = None
    source_center_key: str | None = None
    transfer_mode: str | None = None
    processing_policy: str | None = None
    cleanup_policy: str | None = None
    media_uploads: list[TransferMediaUploadPayload] | None = None
    case_resolution: TransferCaseResolutionPayload | None = None
    custom_marker: str | None = None


def _validated_json_payload(model_cls: type[BaseModel], value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("provenance must be a JSON object")
    try:
        model = model_cls.model_validate(value)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    return model.model_dump(mode="json", exclude_none=True)


def validate_upload_provenance_payload(value: Any) -> dict[str, Any]:
    return _validated_json_payload(UploadProvenancePayload, value)


def validate_transfer_provenance_payload(value: Any) -> dict[str, Any]:
    return _validated_json_payload(TransferProvenancePayload, value)


__all__ = [
    "LocalStudyServerPreanonymizedIngestPayload",
    "PreanonymizedIngestPayload",
    "TransferCaseResolutionPayload",
    "TransferMediaUploadPayload",
    "TransferProvenancePayload",
    "UploadProvenancePayload",
    "validate_transfer_provenance_payload",
    "validate_upload_provenance_payload",
]
