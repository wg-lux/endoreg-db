from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


VIDEO_ANONYMIZATION_CORRECTION_JOB_KIND: Literal["video_anonymization_correction"] = (
    "video_anonymization_correction"
)
MAX_SEGMENTATION_FPS = 50.0
FPS_NORMALIZATION_CONFIG_OPERATION = "segmentation_fps_normalization"


class VideoCorrectionRoi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    width: int
    height: int


class VideoCorrectionRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["device", "custom"]
    device_name: str
    roi: VideoCorrectionRoi | None = None


class VideoAnonymizationCorrectionJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_kind: Literal["video_anonymization_correction"] = (
        VIDEO_ANONYMIZATION_CORRECTION_JOB_KIND
    )
    strategy: Literal["detector_assisted", "processor_region"]
    processing_method: Literal["streaming", "direct"]
    region: VideoCorrectionRegion
    human_review_required: Literal[True]
    apply_all_frames: Literal[True]
    queue: str


class FpsNormalizationHistoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: str = FPS_NORMALIZATION_CONFIG_OPERATION
    max_fps: float = MAX_SEGMENTATION_FPS
    queue: str


__all__ = [
    "FPS_NORMALIZATION_CONFIG_OPERATION",
    "FpsNormalizationHistoryConfig",
    "MAX_SEGMENTATION_FPS",
    "VIDEO_ANONYMIZATION_CORRECTION_JOB_KIND",
    "VideoAnonymizationCorrectionJobConfig",
    "VideoCorrectionRegion",
    "VideoCorrectionRoi",
]
