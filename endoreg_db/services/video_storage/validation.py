from __future__ import annotations

import math
from bisect import bisect_left
from datetime import UTC, datetime

from endoreg_db.schemas.video_storage import (
    FramePresentationTimestamp,
    HlsSegmentBoundary,
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


def _timeline_time_base_seconds(timeline: VideoTimelineContract) -> float:
    if timeline.time_base_num is None or timeline.time_base_den is None:
        raise VideoStorageNormalizationError(
            "HLS presentation-timestamp validation requires source and output "
            "time-base metadata"
        )
    return timeline.time_base_num / timeline.time_base_den


def _relative_presentation_times(
    timestamps: list[FramePresentationTimestamp],
    *,
    expected_frame_count: int,
    label: str,
) -> list[float]:
    if len(timestamps) != expected_frame_count:
        raise VideoStorageNormalizationError(
            f"{label} presentation-timestamp count does not match frame count: "
            f"{len(timestamps)}!={expected_frame_count}"
        )
    first = timestamps[0].presentation_time_seconds
    return [timestamp.presentation_time_seconds - first for timestamp in timestamps]


def _nearest_timestamp_drift(
    timestamps: list[float],
    *,
    target: float,
) -> float:
    insertion_index = bisect_left(timestamps, target)
    candidates: list[float] = []
    if insertion_index < len(timestamps):
        candidates.append(abs(timestamps[insertion_index] - target))
    if insertion_index > 0:
        candidates.append(abs(timestamps[insertion_index - 1] - target))
    if not candidates:
        raise VideoStorageNormalizationError(
            "HLS output has no presentation timestamps for segment validation"
        )
    return min(candidates)


def validate_hls_segment_and_pts_equivalence(
    *,
    source_timeline: VideoTimelineContract,
    output_timeline: VideoTimelineContract,
    source_timestamps: list[FramePresentationTimestamp],
    output_timestamps: list[FramePresentationTimestamp],
    segments: list[HlsSegmentBoundary],
    profile: VideoStorageProfile,
) -> None:
    """Fail closed unless frames and HLS segment boundaries preserve the PTS map."""
    if not segments:
        raise VideoStorageNormalizationError("HLS playlist has no segment boundaries")
    source_relative = _relative_presentation_times(
        source_timestamps,
        expected_frame_count=source_timeline.frame_count,
        label="Source",
    )
    output_relative = _relative_presentation_times(
        output_timestamps,
        expected_frame_count=output_timeline.frame_count,
        label="Output",
    )
    if len(source_relative) != len(output_relative):
        raise VideoStorageNormalizationError(
            "HLS source and output presentation-timestamp counts differ"
        )

    source_time_base = _timeline_time_base_seconds(source_timeline)
    output_time_base = _timeline_time_base_seconds(output_timeline)
    timestamp_tolerance = max(source_time_base, output_time_base) * 2.0 + 1e-6
    for frame_index, (source_time, output_time) in enumerate(
        zip(source_relative, output_relative, strict=True)
    ):
        drift = abs(source_time - output_time)
        if drift > timestamp_tolerance:
            raise VideoStorageNormalizationError(
                "HLS presentation timestamp drifted at frame "
                f"{frame_index}: {drift:.9f}s>{timestamp_tolerance:.9f}s"
            )

    expected_start = 0.0
    segment_boundary_tolerance = max(
        timestamp_tolerance,
        1.0 / source_timeline.fps,
        1.0 / output_timeline.fps,
    )
    for expected_index, segment in enumerate(segments):
        if segment.segment_index != expected_index:
            raise VideoStorageNormalizationError(
                "HLS playlist segment indexes are not contiguous"
            )
        if abs(segment.start_timestamp_seconds - expected_start) > 1e-9:
            raise VideoStorageNormalizationError(
                "HLS playlist segment boundaries are not contiguous"
            )
        expected_start = segment.end_timestamp_seconds
        if expected_index == len(segments) - 1:
            continue
        boundary_drift = _nearest_timestamp_drift(
            output_relative,
            target=segment.end_timestamp_seconds,
        )
        if boundary_drift > segment_boundary_tolerance:
            raise VideoStorageNormalizationError(
                "HLS segment boundary does not map to an output presentation "
                f"timestamp: segment={expected_index} "
                f"drift={boundary_drift:.9f}s "
                f"allowed={segment_boundary_tolerance:.9f}s"
            )

    allowed_duration_drift = max(
        profile.max_duration_drift_seconds,
        1.0 / output_timeline.fps,
    )
    playlist_duration_drift = abs(
        segments[-1].end_timestamp_seconds - output_timeline.duration_seconds
    )
    if playlist_duration_drift > allowed_duration_drift:
        raise VideoStorageNormalizationError(
            "HLS playlist duration drifted from the probed output timeline by "
            f"{playlist_duration_drift:.6f}s "
            f"(allowed {allowed_duration_drift:.6f}s)"
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
