"""Backward-compatible facade for video storage normalization.

The implementation is split by responsibility under :mod:`video_storage`.
Existing imports from this module remain stable.
"""

from __future__ import annotations

import json as json
import shutil as shutil
import subprocess as subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from endoreg_db.schemas.video_storage import (
    SegmentTimelineReference,
    VideoStorageNormalizationEvidence,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageCapacityReport,
    VideoStorageInventoryReport,
    VideoStorageNormalizationError,
    VideoStorageProfile,
    configured_video_storage_profile,
    evidence_as_json,
    video_storage_capacity as _video_storage_capacity,
)
from endoreg_db.services.video_storage.inventory import (
    inventory_video_storage,
    raw_cleanup_blockers,
    video_normalization_evidence,
)
from endoreg_db.services.video_storage.normalization import (
    ensure_video_file_profile as _ensure_video_file_profile,
    normalize_video_file as _normalize_video_file,
)
from endoreg_db.services.video_storage.probes import (
    probe_video_artifact,
    probe_video_frame_pts,
    probe_video_frame_timestamps,
)
from endoreg_db.services.video_storage.timelines import (
    persist_video_source_timeline as _persist_video_source_timeline,
    segment_timeline_references,
    timeline_from_video_metadata,
)
from endoreg_db.services.video_storage.validation import (
    assert_storage_compliance,
    assert_temporal_equivalence,
    validate_annotation_fps_resample,
    validate_normalized_output,
)
from endoreg_db.utils import ffmpeg_wrapper as ffmpeg_wrapper

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def persist_video_source_timeline(video: "VideoFile", path: Path) -> None:
    """Persist PTS using facade dependencies to retain patch compatibility."""
    _persist_video_source_timeline(
        video,
        path,
        probe_artifact=probe_video_artifact,
        probe_frame_pts=probe_video_frame_pts,
        probe_frame_timestamps=probe_video_frame_timestamps,
    )


def normalize_video_file(
    *,
    input_path: Path,
    reference_path: Path,
    quality_mode: str,
    profile: VideoStorageProfile | None = None,
    segments: list[SegmentTimelineReference] | None = None,
    force_cpu: bool = False,
) -> VideoStorageNormalizationEvidence:
    """Normalize a video through the modular implementation."""
    selected_profile = profile or configured_video_storage_profile()
    return _normalize_video_file(
        input_path=input_path,
        reference_path=reference_path,
        quality_mode=quality_mode,
        profile=selected_profile,
        segments=segments,
        force_cpu=force_cpu,
        probe_artifact=probe_video_artifact,
        validate_output=validate_normalized_output,
    )


def ensure_video_file_profile(
    *,
    input_path: Path,
    output_path: Path,
    reference_path: Path,
    quality_mode: str,
    profile: VideoStorageProfile | None = None,
    segments: list[SegmentTimelineReference] | None = None,
    force_cpu: bool = False,
) -> VideoStorageNormalizationEvidence:
    """Publish a compliant output while avoiding unnecessary re-encoding."""
    selected_profile = profile or configured_video_storage_profile()
    return _ensure_video_file_profile(
        input_path=input_path,
        output_path=output_path,
        reference_path=reference_path,
        quality_mode=quality_mode,
        profile=selected_profile,
        segments=segments,
        force_cpu=force_cpu,
        probe_artifact=probe_video_artifact,
        validate_output=validate_normalized_output,
    )


def video_storage_capacity(
    *,
    storage_root: Path,
    projected_temporary_bytes: int = 0,
) -> VideoStorageCapacityReport:
    """Report capacity while retaining facade-level dependency patching."""
    return _video_storage_capacity(
        storage_root=storage_root,
        projected_temporary_bytes=projected_temporary_bytes,
    )


__all__ = [
    "VideoStorageCapacityReport",
    "VideoStorageInventoryReport",
    "VideoStorageNormalizationError",
    "VideoStorageProfile",
    "assert_storage_compliance",
    "assert_temporal_equivalence",
    "configured_video_storage_profile",
    "evidence_as_json",
    "ensure_video_file_profile",
    "inventory_video_storage",
    "normalize_video_file",
    "persist_video_source_timeline",
    "probe_video_artifact",
    "probe_video_frame_pts",
    "raw_cleanup_blockers",
    "segment_timeline_references",
    "timeline_from_video_metadata",
    "validate_annotation_fps_resample",
    "validate_normalized_output",
    "video_normalization_evidence",
    "video_storage_capacity",
]
