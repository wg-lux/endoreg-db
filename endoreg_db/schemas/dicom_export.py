from __future__ import annotations

# This module mirrors the shared lx-dtypes contract until the next lx-dtypes
# release is available to the pinned endoreg-db runtime.
from datetime import date, datetime
import re
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


_DICOM_UID_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DicomValidationResult(_ContractModel):
    validator_name: str = Field(min_length=1, max_length=128)
    validator_version: str = Field(min_length=1, max_length=64)
    status: Literal["passed"]


class DicomDeidentification(_ContractModel):
    profile: str = Field(min_length=1, max_length=255)
    method: str = Field(min_length=1, max_length=255)
    patient_identity_removed: Literal[True]
    clean_pixel_data: bool


def _validate_uid(value: str) -> str:
    if len(value) > 64 or _DICOM_UID_PATTERN.fullmatch(value) is None:
        raise ValueError("DICOM UIDs must be numeric dotted values up to 64 chars")
    return value


class DicomInstanceManifest(_ContractModel):
    sop_instance_uid: str
    sop_class_uid: str
    transfer_syntax_uid: str
    instance_number: int | None = Field(default=None, ge=1)
    artifact_reference: str = Field(min_length=1, max_length=1024)
    artifact_class: Literal["anonymized_processed"]
    artifact_sha256: str
    size_bytes: int = Field(ge=1)
    masked_regions: int = Field(default=0, ge=0)

    @field_validator("sop_instance_uid", "sop_class_uid", "transfer_syntax_uid")
    @classmethod
    def validate_uid(cls, value: str) -> str:
        return _validate_uid(value)

    @field_validator("artifact_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if _SHA256_PATTERN.fullmatch(normalized) is None:
            raise ValueError("artifact_sha256 must be a SHA-256 hex digest")
        return normalized

    @field_validator("artifact_reference")
    @classmethod
    def validate_artifact_reference(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError(
                "artifact_reference must be a relative protected-storage key"
            )
        return value


class DicomSeriesManifest(_ContractModel):
    series_instance_uid: str
    modality: str = Field(min_length=1, max_length=16)
    series_number: int | None = Field(default=None, ge=1)
    instances: list[DicomInstanceManifest] = Field(min_length=1)

    @field_validator("series_instance_uid")
    @classmethod
    def validate_uid(cls, value: str) -> str:
        return _validate_uid(value)

    @field_validator("modality")
    @classmethod
    def normalize_modality(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_instance_uids(self) -> Self:
        uids = [item.sop_instance_uid for item in self.instances]
        if len(uids) != len(set(uids)):
            raise ValueError("SOP Instance UIDs must be unique within a series")
        return self


class DicomStudyManifest(_ContractModel):
    study_instance_uid: str
    patient_pseudonym: str = Field(min_length=1, max_length=255)
    accession_identifier: str | None = Field(default=None, max_length=255)
    study_date: date | None = None
    series: list[DicomSeriesManifest] = Field(min_length=1)

    @field_validator("study_instance_uid")
    @classmethod
    def validate_uid(cls, value: str) -> str:
        return _validate_uid(value)

    @model_validator(mode="after")
    def validate_series_uids(self) -> Self:
        series_uids = [item.series_instance_uid for item in self.series]
        if len(series_uids) != len(set(series_uids)):
            raise ValueError("Series Instance UIDs must be unique within a study")
        sop_uids = [
            instance.sop_instance_uid
            for series in self.series
            for instance in series.instances
        ]
        if len(sop_uids) != len(set(sop_uids)):
            raise ValueError("SOP Instance UIDs must be unique within an export")
        return self


class DicomExportManifestV2(_ContractModel):
    schema_version: Literal[2]
    export_id: UUID
    created_at: datetime
    source_system: str = Field(min_length=1, max_length=128)
    deidentification: DicomDeidentification
    validation: DicomValidationResult
    study: DicomStudyManifest

    @field_validator("created_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        return value


def validate_dicom_export_manifest_v2(value: object) -> DicomExportManifestV2:
    try:
        return DicomExportManifestV2.model_validate(value)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def dump_dicom_export_manifest_v2(value: object) -> dict[str, object]:
    return validate_dicom_export_manifest_v2(value).model_dump(
        mode="json", exclude_none=True
    )


__all__ = [
    "DicomExportManifestV2",
    "DicomInstanceManifest",
    "DicomSeriesManifest",
    "DicomStudyManifest",
    "dump_dicom_export_manifest_v2",
    "validate_dicom_export_manifest_v2",
]
