"""Manifest written beside ``pts_dataset_v1`` frame annotation exports.

Segment boundaries reference the same persisted frame presentation timestamps
as the annotation rows, so every row of a segment satisfies
``start_presentation_timestamp <= presentation_timestamp <= end_presentation_timestamp``.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from endoreg_db.schemas.video_storage import VideoExportFileNameSource

FRAME_ANNOTATION_EXPORT_MANIFEST_NAME = "export_manifest.json"

# Whether exported ticks are the ticks recorded at import: ``identical_ticks``
# values match external clocks unchanged; ``identical_instants`` are equal times
# in the stated time base; ``changed`` means an earlier transcode re-timed frames,
# so the ticks are consistent with the exported video but not with the original
# recording clock; ``unverified`` lacks decoder ancestry.
type RecordingTimestampIdentity = Literal[
    "identical_ticks", "identical_instants", "changed", "unverified"
]


class FrameAnnotationExportVideo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    video_id: int = Field(gt=0)
    video_file_name: str = Field(min_length=1)
    video_file_name_source: VideoExportFileNameSource
    video_file_exported: bool
    artifact_sha256: str = Field(min_length=1)
    stream_time_base_num: int = Field(gt=0)
    stream_time_base_den: int = Field(gt=0)
    recording_timestamp_identity: RecordingTimestampIdentity
    recording_started_at: str | None
    recording_ended_at: str | None


class FrameAnnotationExportSegment(BaseModel):
    """Inclusive first/last frame of a segment on the processed generation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    segment_id: int = Field(gt=0)
    video_id: int = Field(gt=0)
    label_id: int = Field(gt=0)
    label_name: str
    information_source_id: int | None
    start_frame_number: int = Field(ge=0)
    end_frame_number: int = Field(ge=0)
    start_presentation_timestamp: int = Field(ge=0)
    end_presentation_timestamp: int = Field(ge=0)
    start_presentation_time_seconds: float = Field(ge=0)
    end_presentation_time_seconds: float = Field(ge=0)
    stream_time_base_num: int = Field(gt=0)
    stream_time_base_den: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_inclusive_order(self) -> Self:
        if self.end_frame_number < self.start_frame_number:
            raise ValueError("Segment end frame precedes its start frame")
        if self.end_presentation_timestamp < self.start_presentation_timestamp:
            raise ValueError("Segment end timestamp precedes its start timestamp")
        return self


class FrameAnnotationExportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    manifest_version: Literal["frame_annotation_export_manifest_v1"] = (
        "frame_annotation_export_manifest_v1"
    )
    export_profile: Literal["pts_dataset_v1"] = "pts_dataset_v1"
    annotation_file_name: str = Field(min_length=1)
    segment_boundaries: Literal["inclusive_first_last_frame"] = (
        "inclusive_first_last_frame"
    )
    videos: list[FrameAnnotationExportVideo]
    segments: list[FrameAnnotationExportSegment]


__all__ = [
    "FRAME_ANNOTATION_EXPORT_MANIFEST_NAME",
    "FrameAnnotationExportManifest",
    "FrameAnnotationExportSegment",
    "FrameAnnotationExportVideo",
    "RecordingTimestampIdentity",
]
