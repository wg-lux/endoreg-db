from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _empty_segment_timeline_references() -> list["SegmentTimelineReference"]:
    return []


class VideoTimelineContract(BaseModel):
    """Timeline coordinates that must survive canonical video normalization."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    fps_num: int = Field(gt=0)
    fps_den: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    frame_count: int = Field(gt=0)
    variable_frame_rate: bool = False
    time_base_num: int | None = Field(default=None, gt=0)
    time_base_den: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_time_base_pair(self) -> Self:
        if (self.time_base_num is None) != (self.time_base_den is None):
            raise ValueError("time_base_num and time_base_den must be set together")
        return self

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den


class FramePresentationTimestamp(BaseModel):
    """Exact video-stream timestamp and its presentation time in seconds."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    presentation_timestamp: int = Field(ge=0)
    presentation_time_seconds: float = Field(ge=0)


class SegmentTimelineReference(BaseModel):
    """Auditable conversion of a persisted segment frame range to timestamps."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    segment_id: int = Field(gt=0)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    start_timestamp_seconds: float = Field(ge=0)
    end_timestamp_seconds: float = Field(gt=0)
    timeline_version: Literal["pts_v1"] = "pts_v1"

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_frame <= self.start_frame:
            raise ValueError("end_frame must be greater than start_frame")
        if self.end_timestamp_seconds <= self.start_timestamp_seconds:
            raise ValueError(
                "end_timestamp_seconds must be greater than start_timestamp_seconds"
            )
        return self


class VideoArtifactProbe(BaseModel):
    """Validated ffprobe and filesystem values used by the storage gate."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    codec_name: str = Field(min_length=1)
    pixel_format: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    bit_rate_bps: int | None = Field(default=None, gt=0)
    size_bytes: int = Field(gt=0)
    timeline: VideoTimelineContract


class VideoStorageNormalizationEvidence(BaseModel):
    """Persisted proof that a canonical output passed storage and timeline gates."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"] = "1"
    profile_name: str = Field(min_length=1)
    normalized_at: datetime
    source: VideoArtifactProbe
    output: VideoArtifactProbe
    segment_timestamps: list[SegmentTimelineReference] = Field(
        default_factory=_empty_segment_timeline_references
    )
    temporal_equivalent: Literal[True]
    storage_compliant: Literal[True]

    @field_validator("normalized_at", mode="before")
    @classmethod
    def parse_json_datetime(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class VideoFpsResamplingEvidence(BaseModel):
    """Provenance for the pre-annotation, coordinate-changing FPS workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"] = "1"
    profile_name: Literal["annotation_fps_resample_v1"] = "annotation_fps_resample_v1"
    normalized_at: datetime
    max_fps: float = Field(gt=0)
    source: VideoArtifactProbe
    output: VideoArtifactProbe
    timeline_version: Literal["pts_v1"] = "pts_v1"

    @field_validator("normalized_at", mode="before")
    @classmethod
    def parse_json_datetime(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class VideoSourceTimelineEvidence(BaseModel):
    """Versioned persisted timeline used for clinical frame coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"] = "1"
    timeline_version: Literal["pts_v1"] = "pts_v1"
    persisted_at: datetime
    source: VideoArtifactProbe
    timestamp_mapping: Literal["ffprobe_pts", "rational_cfr"]

    @field_validator("persisted_at", mode="before")
    @classmethod
    def parse_json_datetime(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class ClinicalFrameQualityEvidence(BaseModel):
    """Explicit reviewer approval required before destructive raw cleanup."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"] = "1"
    profile_name: str = Field(min_length=1)
    approved: Literal[True]
    approved_at: datetime
    approved_by: str = Field(min_length=1)
    benchmark_reference: str = Field(min_length=1)

    @field_validator("approved_at", mode="before")
    @classmethod
    def parse_json_datetime(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


__all__ = [
    "FramePresentationTimestamp",
    "SegmentTimelineReference",
    "ClinicalFrameQualityEvidence",
    "VideoArtifactProbe",
    "VideoFpsResamplingEvidence",
    "VideoStorageNormalizationEvidence",
    "VideoSourceTimelineEvidence",
    "VideoTimelineContract",
]
