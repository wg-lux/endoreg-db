"""Shared DICOM contracts with the host's strict scalar ingress policy."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import cast

from lx_dtypes.models.contracts import dicom_export as shared
from lx_dtypes.models.contracts.dicom_export import (
    DicomValidationResult as DicomValidationResult,
)
from pydantic import Field, TypeAdapter, ValidationError, field_validator

DICOM_EXPORT_MANIFEST_SCHEMA_VERSION = 2


class UnsupportedDicomManifestVersionError(ValueError):
    """Raised before payload validation when the manifest version is unsupported."""


class DicomDeidentification(shared.DicomDeidentification):
    clean_pixel_data: bool = Field(strict=True)

    @field_validator("patient_identity_removed", mode="before")
    @classmethod
    def require_literal_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("patient_identity_removed must be true")
        return value


class DicomInstanceManifest(shared.DicomInstanceManifest):
    instance_number: int | None = Field(default=None, ge=1, strict=True)
    size_bytes: int = Field(ge=1, strict=True)
    masked_regions: int = Field(default=0, ge=0, strict=True)


class DicomSeriesManifest(shared.DicomSeriesManifest):
    series_number: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("instances", mode="before")
    @classmethod
    def validate_host_instances(cls, value: object) -> list[DicomInstanceManifest]:
        return TypeAdapter(list[DicomInstanceManifest]).validate_python(value)


class DicomStudyManifest(shared.DicomStudyManifest):
    @field_validator("series", mode="before")
    @classmethod
    def validate_host_series(cls, value: object) -> list[DicomSeriesManifest]:
        return TypeAdapter(list[DicomSeriesManifest]).validate_python(value)


class DicomExportManifestV2(shared.DicomExportManifestV2):
    @field_validator("deidentification", mode="before")
    @classmethod
    def validate_host_deidentification(cls, value: object) -> DicomDeidentification:
        return DicomDeidentification.model_validate(value)

    @field_validator("study", mode="before")
    @classmethod
    def validate_host_study(cls, value: object) -> DicomStudyManifest:
        return DicomStudyManifest.model_validate(value)


def validate_dicom_export_manifest_v2(value: object) -> DicomExportManifestV2:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        raw_version: object | None = mapping.get("schema_version")
        if raw_version != DICOM_EXPORT_MANIFEST_SCHEMA_VERSION or isinstance(
            raw_version, bool
        ):
            display_version = (
                repr(raw_version)
                if raw_version is None or isinstance(raw_version, (str, int))
                else type(raw_version).__name__
            )
            raise UnsupportedDicomManifestVersionError(
                "unsupported DICOM manifest schema_version "
                f"{display_version}; supported version is "
                f"{DICOM_EXPORT_MANIFEST_SCHEMA_VERSION}"
            )
    try:
        return DicomExportManifestV2.model_validate(value)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def dump_dicom_export_manifest_v2(value: object) -> dict[str, object]:
    return validate_dicom_export_manifest_v2(value).model_dump(
        mode="json", exclude_none=True
    )


def dicom_export_manifest_sha256(value: object) -> str:
    canonical = json.dumps(
        dump_dicom_export_manifest_v2(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "DICOM_EXPORT_MANIFEST_SCHEMA_VERSION",
    "DicomExportManifestV2",
    "DicomInstanceManifest",
    "DicomSeriesManifest",
    "DicomStudyManifest",
    "UnsupportedDicomManifestVersionError",
    "dicom_export_manifest_sha256",
    "dump_dicom_export_manifest_v2",
    "validate_dicom_export_manifest_v2",
]
