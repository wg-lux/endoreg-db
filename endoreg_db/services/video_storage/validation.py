from __future__ import annotations

import math
from datetime import UTC, datetime

from endoreg_db.schemas.video_storage import (
    SegmentTimelineReference,
    VideoArtifactProbe,
    VideoFpsResamplingEvidence,
    VideoStorageNormalizationEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_storage.contracts import (
    VideoStorageNormalizationError,
    VideoStorageProfile,
)
from endoreg_db.utils.video.encoding_standard import STANDARD_VIDEO_ENCODING


def assert_temporal_equivalence(
    source: VideoTimelineContract,
    output: VideoTimelineContract,
    *,
    profile: VideoStorageProfile,
) -> None:
    if source.variable_frame_rate or output.variable_frame_rate:
        if source.time_base_num is None or output.time_base_num is None:
            raise VideoStorageNormalizationError(
                "Variable-frame-rate video requires source and output time-base metadata"
            )
    if not math.isclose(
        source.fps,
        output.fps,
        rel_tol=profile.fps_relative_tolerance,
        abs_tol=0.001,
    ):
        raise VideoStorageNormalizationError(
            f"Output FPS drifted from {source.fps:g} to {output.fps:g}"
        )
    if source.frame_count != output.frame_count:
        raise VideoStorageNormalizationError(
            "Output frame count drifted from "
            f"{source.frame_count} to {output.frame_count}"
        )
    allowed_duration_drift = max(
        profile.max_duration_drift_seconds,
        1.0 / source.fps,
    )
    duration_drift = abs(source.duration_seconds - output.duration_seconds)
    if duration_drift > allowed_duration_drift:
        raise VideoStorageNormalizationError(
            "Output duration drifted by "
            f"{duration_drift:.6f}s (allowed {allowed_duration_drift:.6f}s)"
        )


def assert_storage_compliance(
    output: VideoArtifactProbe,
    *,
    profile: VideoStorageProfile,
) -> None:
    if output.width > profile.max_width or output.height > profile.max_height:
        raise VideoStorageNormalizationError(
            "Output dimensions exceed profile: "
            f"{output.width}x{output.height}>"
            f"{profile.max_width}x{profile.max_height}"
        )
    if output.timeline.fps > profile.max_source_fps:
        raise VideoStorageNormalizationError(
            "Output FPS exceeds profile: "
            f"{output.timeline.fps:g}>{profile.max_source_fps:g}"
        )
    if output.width > profile.max_width or output.height > profile.max_height:
        raise VideoStorageNormalizationError(
            "Output dimensions exceed profile: "
            f"{output.width}x{output.height}>"
            f"{profile.max_width}x{profile.max_height}"
        )
    if output.timeline.fps > profile.max_source_fps:
        raise VideoStorageNormalizationError(
            "Output FPS exceeds profile: "
            f"{output.timeline.fps:g}>{profile.max_source_fps:g}"
        )
    if output.codec_name != STANDARD_VIDEO_ENCODING.codec_name:
        raise VideoStorageNormalizationError(
            f"Output codec {output.codec_name!r} is not H.264"
        )
    if output.pixel_format not in {STANDARD_VIDEO_ENCODING.pixel_format, "yuvj420p"}:
        raise VideoStorageNormalizationError(
            f"Output pixel format {output.pixel_format!r} is not YUV420P"
        )
    if (
        output.bit_rate_bps is not None
        and output.bit_rate_bps > profile.max_bit_rate_bps
    ):
        raise VideoStorageNormalizationError(
            "Output bitrate exceeds profile: "
            f"{output.bit_rate_bps}>{profile.max_bit_rate_bps}"
        )
    maximum_size = profile.maximum_file_size(output.timeline.duration_seconds)
    if output.size_bytes > maximum_size:
        raise VideoStorageNormalizationError(
            f"Output size exceeds profile: {output.size_bytes}>{maximum_size}"
        )


def validate_normalized_output(
    *,
    source: VideoArtifactProbe,
    output: VideoArtifactProbe,
    profile: VideoStorageProfile,
    segments: list[SegmentTimelineReference] | None = None,
) -> VideoStorageNormalizationEvidence:
    if source.width > profile.max_width or source.height > profile.max_height:
        raise VideoStorageNormalizationError(
            "Source dimensions exceed profile and require explicit quarantine or "
            "a separately approved resize profile"
        )
    if source.timeline.fps > profile.max_source_fps:
        raise VideoStorageNormalizationError(
            "Source FPS exceeds profile and requires explicit quarantine or a "
            "separately approved resampling profile"
        )
    if output.width != source.width or output.height != source.height:
        raise VideoStorageNormalizationError(
            "Canonical normalization must preserve source dimensions until the "
            "clinical frame-quality benchmark approves a resize profile"
        )
    assert_temporal_equivalence(source.timeline, output.timeline, profile=profile)
    assert_storage_compliance(output, profile=profile)
    return VideoStorageNormalizationEvidence(
        profile_name=profile.name,
        normalized_at=datetime.now(UTC),
        source=source,
        output=output,
        segment_timestamps=list(segments or []),
        temporal_equivalent=True,
        storage_compliant=True,
    )


def validate_annotation_fps_resample(
    *,
    source: VideoArtifactProbe,
    output: VideoArtifactProbe,
    max_fps: float,
    profile: VideoStorageProfile,
) -> VideoFpsResamplingEvidence:
    if not math.isfinite(max_fps) or max_fps <= 0:
        raise ValueError("max_fps must be finite and positive")
    if not math.isclose(
        max_fps,
        profile.annotation_max_fps,
        rel_tol=0.0,
        abs_tol=0.001,
    ):
        raise VideoStorageNormalizationError(
            "Annotation FPS target does not match the typed storage profile: "
            f"{max_fps:g}!={profile.annotation_max_fps:g}"
        )
    if source.timeline.fps <= max_fps:
        raise VideoStorageNormalizationError(
            "Annotation FPS resampling is only valid above the target FPS"
        )
    if output.width != source.width or output.height != source.height:
        raise VideoStorageNormalizationError(
            "Annotation FPS resampling must preserve source dimensions"
        )
    if output.timeline.variable_frame_rate:
        raise VideoStorageNormalizationError(
            "Annotation FPS resampling must publish a constant-frame-rate output"
        )
    if output.timeline.fps > max_fps or not math.isclose(
        output.timeline.fps,
        max_fps,
        rel_tol=0.001,
        abs_tol=0.01,
    ):
        raise VideoStorageNormalizationError(
            f"Resampled output FPS {output.timeline.fps:g} does not match {max_fps:g}"
        )
    allowed_duration_drift = max(
        profile.max_duration_drift_seconds,
        1.0 / source.timeline.fps,
    )
    if (
        abs(source.timeline.duration_seconds - output.timeline.duration_seconds)
        > allowed_duration_drift
    ):
        raise VideoStorageNormalizationError(
            "FPS resampling changed the video duration beyond tolerance"
        )
    expected_frames = round(output.timeline.duration_seconds * max_fps)
    if abs(output.timeline.frame_count - expected_frames) > 1:
        raise VideoStorageNormalizationError(
            "FPS resampling produced an inconsistent frame count: "
            f"{output.timeline.frame_count} versus expected {expected_frames}"
        )
    assert_storage_compliance(output, profile=profile)
    return VideoFpsResamplingEvidence(
        normalized_at=datetime.now(UTC),
        max_fps=max_fps,
        source=source,
        output=output,
    )
