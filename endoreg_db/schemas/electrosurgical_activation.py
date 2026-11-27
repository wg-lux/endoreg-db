"""Host-owned source identity and recording binding for activation summaries."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from typing import Literal, Self

from lx_dtypes.models.contracts.video_provenance import RecordingSynchronization
from lx_dtypes.models.contracts.electrosurgical_activations import (
    ElectrosurgicalActivation,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActivationSourceFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ActivationImportMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    parser_id: Literal["activation_summary_import_v1"] = "activation_summary_import_v1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_format: ActivationSourceFormat
    source_timezone: str = Field(min_length=1, max_length=64)
    activation_count: int = Field(ge=1, le=100_000)
    synchronization: RecordingSynchronization
    # The electrical side of the anchors uses UTC microseconds since Unix epoch.
    clock_basis: Literal["unix_microseconds"] = "unix_microseconds"
    timestamp_rounding_error_seconds: Decimal = Field(ge=0, allow_inf_nan=False)
    max_timing_error_seconds: Decimal = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_clock_and_error(self) -> Self:
        sync = self.synchronization
        if Fraction(
            sync.electrical_time_base_num, sync.electrical_time_base_den
        ) != Fraction(1, 1_000_000):
            raise ValueError("Activation anchors require UTC Unix-microsecond ticks")
        error = Fraction(sync.uncertainty_num, sync.uncertainty_den) + Fraction(
            self.timestamp_rounding_error_seconds
        )
        if error > Fraction(self.max_timing_error_seconds):
            raise ValueError(
                "Synchronization and timestamp error exceed the supplied tolerance"
            )
        return self


class ActivationMappingStatus(StrEnum):
    MAPPED = "mapped"
    OUTSIDE_SYNCHRONIZATION = "outside_synchronization"
    OUTSIDE_RECORDING = "outside_recording"
    UNBOUNDED_INTERVAL = "unbounded_interval"


class ActivationFrameCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    activation_index: int = Field(ge=0)
    status: ActivationMappingStatus
    summary: ElectrosurgicalActivation | None = None
    first_frame: int | None = Field(default=None, ge=0)
    end_frame_exclusive: int | None = Field(default=None, ge=0)
    start_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        values = (
            self.summary,
            self.first_frame,
            self.end_frame_exclusive,
            self.start_seconds,
            self.end_seconds,
        )
        if self.status == ActivationMappingStatus.MAPPED:
            if any(value is None for value in values):
                raise ValueError(
                    "Mapped activations require summary and frame coverage"
                )
            if (
                self.end_frame_exclusive is not None
                and self.first_frame is not None
                and self.end_frame_exclusive < self.first_frame
            ):
                raise ValueError("Activation frame coverage is reversed")
            if (
                self.end_seconds is not None
                and self.start_seconds is not None
                and self.end_seconds < self.start_seconds
            ):
                raise ValueError("Activation presentation coverage is reversed")
        elif any(value is not None for value in values):
            raise ValueError(
                "Unmapped activations must not expose out-of-recording summaries"
            )
        return self


class ActivationPageRange(BaseModel):
    first_activation: int = Field(ge=0)
    activation_count: int = Field(ge=1, le=500)
    total_activation_count: int = Field(ge=1)
    next_first_activation: int | None = Field(ge=0)


class ActivationAlignedPage(BaseModel):
    video_id: int
    activation_log_id: int
    source_sha256: str
    generation_content_hash: str
    timeline_version: Literal["pts_v1"] = "pts_v1"
    algorithm: Literal["activation_frame_coverage_v1"] = "activation_frame_coverage_v1"
    timing_error_seconds: Decimal
    range: ActivationPageRange
    activations: list[ActivationFrameCoverage]
