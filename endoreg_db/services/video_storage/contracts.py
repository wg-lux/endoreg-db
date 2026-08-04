from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from endoreg_db.config.env import (
    get_video_storage_annotation_max_fps,
    get_video_storage_fixed_overhead_bytes,
    get_video_storage_max_bit_rate_bps,
    get_video_storage_max_bytes_per_second,
    get_video_storage_max_height,
    get_video_storage_max_source_fps,
    get_video_storage_max_width,
    get_video_storage_stop_free_bytes,
    get_video_storage_warning_free_bytes,
)
from endoreg_db.utils.video.encoding_standard import STANDARD_VIDEO_ENCODING


class VideoStorageNormalizationError(RuntimeError):
    """Raised when an output cannot satisfy the storage or timeline contract."""


@dataclass(frozen=True, slots=True)
class VideoStorageProfile:
    name: str
    max_bit_rate_bps: int
    max_bytes_per_second: int
    fixed_overhead_bytes: int
    max_width: int = 4096
    max_height: int = 2160
    max_source_fps: float = 120.0
    annotation_max_fps: float = 50.0
    max_duration_drift_seconds: float = 0.1
    fps_relative_tolerance: float = 0.001

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Video storage profile name must not be empty")
        if self.max_bit_rate_bps <= 0 or self.max_bytes_per_second <= 0:
            raise ValueError("Video storage bitrate and byte budgets must be positive")
        if self.fixed_overhead_bytes < 0:
            raise ValueError("Video storage fixed overhead must not be negative")
        if self.max_width <= 0 or self.max_height <= 0:
            raise ValueError("Video storage dimensions must be positive")
        if not math.isfinite(self.max_source_fps) or self.max_source_fps <= 0:
            raise ValueError("Maximum source FPS must be finite and positive")
        if not math.isfinite(self.annotation_max_fps) or self.annotation_max_fps <= 0:
            raise ValueError("Annotation maximum FPS must be finite and positive")
        if self.annotation_max_fps > self.max_source_fps:
            raise ValueError(
                "Annotation maximum FPS must not exceed source maximum FPS"
            )
        if self.max_duration_drift_seconds < 0:
            raise ValueError("Duration drift tolerance must not be negative")
        if self.fps_relative_tolerance <= 0:
            raise ValueError("FPS tolerance must be positive")

    def ffmpeg_output_args(self, *, target_fps: float | None = None) -> list[str]:
        max_rate = str(self.max_bit_rate_bps)
        filter_chain = STANDARD_VIDEO_ENCODING.filter_chain()
        fps_mode = "passthrough"
        if target_fps is not None:
            if not math.isfinite(target_fps) or target_fps <= 0:
                raise ValueError("target_fps must be finite and positive")
            if target_fps > self.annotation_max_fps:
                raise ValueError(
                    "target_fps exceeds the annotation FPS limit: "
                    f"{target_fps:g}>{self.annotation_max_fps:g}"
                )
            filter_chain = f"{filter_chain},fps={target_fps:g}"
            fps_mode = "cfr"
        return [
            "-profile:v",
            STANDARD_VIDEO_ENCODING.profile,
            "-vf",
            filter_chain,
            "-pix_fmt",
            STANDARD_VIDEO_ENCODING.pixel_format,
            "-color_range",
            STANDARD_VIDEO_ENCODING.color_range,
            "-fps_mode",
            fps_mode,
            "-maxrate",
            max_rate,
            "-bufsize",
            str(self.max_bit_rate_bps * 2),
            "-movflags",
            "+faststart",
        ]

    def maximum_file_size(self, duration_seconds: float) -> int:
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("Video duration must be finite and positive")
        return (
            math.ceil(duration_seconds * self.max_bytes_per_second)
            + self.fixed_overhead_bytes
        )


@dataclass(frozen=True, slots=True)
class VideoStorageInventoryReport:
    video_id: int
    raw_bytes: int
    processed_bytes: int
    raw_streamable_bytes: int
    processed_streamable_bytes: int
    raw_hls_bytes: int
    processed_hls_bytes: int
    anonymization_validated: bool
    normalization_verified: bool
    raw_cleanup_ready: bool
    referenced_artifacts: int = 0
    missing_referenced_artifacts: int = 0

    @property
    def total_bytes(self) -> int:
        return (
            self.raw_bytes
            + self.processed_bytes
            + self.raw_streamable_bytes
            + self.processed_streamable_bytes
            + self.raw_hls_bytes
            + self.processed_hls_bytes
        )

    @property
    def reclaimable_raw_bytes(self) -> int:
        if not self.anonymization_validated or not self.raw_cleanup_ready:
            return 0
        return self.raw_bytes + self.raw_streamable_bytes + self.raw_hls_bytes

    @property
    def reconciled(self) -> bool:
        return self.missing_referenced_artifacts == 0

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "video_id": self.video_id,
            "raw_bytes": self.raw_bytes,
            "processed_bytes": self.processed_bytes,
            "raw_streamable_bytes": self.raw_streamable_bytes,
            "processed_streamable_bytes": self.processed_streamable_bytes,
            "raw_hls_bytes": self.raw_hls_bytes,
            "processed_hls_bytes": self.processed_hls_bytes,
            "total_bytes": self.total_bytes,
            "reclaimable_raw_bytes": self.reclaimable_raw_bytes,
            "anonymization_validated": self.anonymization_validated,
            "normalization_verified": self.normalization_verified,
            "raw_cleanup_ready": self.raw_cleanup_ready,
            "referenced_artifacts": self.referenced_artifacts,
            "missing_referenced_artifacts": self.missing_referenced_artifacts,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True, slots=True)
class VideoStorageCapacityReport:
    total_bytes: int
    used_bytes: int
    free_bytes: int
    projected_temporary_bytes: int
    warning_free_bytes: int
    stop_free_bytes: int
    status: Literal["ok", "warning", "stop"]

    def as_dict(self) -> dict[str, int | str]:
        return {
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "projected_temporary_bytes": self.projected_temporary_bytes,
            "projected_free_bytes": max(
                0, self.free_bytes - self.projected_temporary_bytes
            ),
            "warning_free_bytes": self.warning_free_bytes,
            "stop_free_bytes": self.stop_free_bytes,
            "status": self.status,
        }


def configured_video_storage_profile() -> VideoStorageProfile:
    return VideoStorageProfile(
        name="clinical_h264_bounded_v1",
        max_bit_rate_bps=get_video_storage_max_bit_rate_bps(),
        max_bytes_per_second=get_video_storage_max_bytes_per_second(),
        fixed_overhead_bytes=get_video_storage_fixed_overhead_bytes(),
        max_width=get_video_storage_max_width(),
        max_height=get_video_storage_max_height(),
        max_source_fps=get_video_storage_max_source_fps(),
        annotation_max_fps=get_video_storage_annotation_max_fps(),
    )


def video_storage_capacity(
    *,
    storage_root: Path,
    projected_temporary_bytes: int = 0,
    warning_free_bytes: int | None = None,
    stop_free_bytes: int | None = None,
) -> VideoStorageCapacityReport:
    if projected_temporary_bytes < 0:
        raise ValueError("Projected temporary bytes must not be negative")
    usage = shutil.disk_usage(Path(storage_root))
    warning_bytes = (
        get_video_storage_warning_free_bytes()
        if warning_free_bytes is None
        else warning_free_bytes
    )
    stop_bytes = (
        get_video_storage_stop_free_bytes()
        if stop_free_bytes is None
        else stop_free_bytes
    )
    projected_free = max(0, usage.free - projected_temporary_bytes)
    status: Literal["ok", "warning", "stop"]
    if projected_free <= stop_bytes:
        status = "stop"
    elif projected_free <= warning_bytes:
        status = "warning"
    else:
        status = "ok"
    return VideoStorageCapacityReport(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        projected_temporary_bytes=projected_temporary_bytes,
        warning_free_bytes=warning_bytes,
        stop_free_bytes=stop_bytes,
        status=status,
    )


def evidence_as_json(evidence: BaseModel) -> dict[str, Any]:
    return evidence.model_dump(mode="json")
