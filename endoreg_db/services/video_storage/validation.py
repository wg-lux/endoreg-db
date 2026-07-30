from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime

from endoreg_db.schemas.video_storage import (
    PresentationTimestampBoundary,
    PresentationTimestampTimeline,
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


class MeasuredAverageFrameRateDriftError(VideoStorageNormalizationError):
    """A nominally stable stream has a different measured average rate."""


@dataclass(frozen=True, slots=True)
class ProvenResampledHlsContext:
    provenance: VideoFpsResamplingEvidence | None
    source_generation_verified: bool
    boundaries: tuple[PresentationTimestampBoundary, ...]


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
        source.nominal_fps,
        output.nominal_fps,
        rel_tol=profile.fps_relative_tolerance,
        abs_tol=0.001,
    ):
        raise VideoStorageNormalizationError(
            "Output nominal FPS drifted from "
            f"{source.nominal_fps:g} to {output.nominal_fps:g}"
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
    if not math.isclose(
        source.measured_average_fps,
        output.measured_average_fps,
        rel_tol=profile.fps_relative_tolerance,
        abs_tol=0.001,
    ):
        raise MeasuredAverageFrameRateDriftError(
            "Output measured average FPS drifted from "
            f"{source.measured_average_fps:g} "
            f"to {output.measured_average_fps:g}"
        )


def _time_base_seconds(timeline: VideoTimelineContract) -> float:
    if timeline.time_base_num is None or timeline.time_base_den is None:
        raise VideoStorageNormalizationError(
            "Proven resampled HLS equivalence requires a stream time base"
        )
    return timeline.time_base_num / timeline.time_base_den


def _assert_provenance_matches_source(
    provenance: VideoFpsResamplingEvidence,
    source: VideoArtifactProbe,
    *,
    profile: VideoStorageProfile,
) -> None:
    expected = provenance.output
    if expected.width != source.width or expected.height != source.height:
        raise VideoStorageNormalizationError(
            "FPS resampling provenance dimensions do not match the HLS source"
        )
    if expected.timeline.frame_count != source.timeline.frame_count:
        raise VideoStorageNormalizationError(
            "FPS resampling provenance frame count does not match the HLS source"
        )
    if not math.isclose(
        expected.timeline.nominal_fps,
        source.timeline.nominal_fps,
        rel_tol=0.0,
        abs_tol=0.001,
    ):
        raise VideoStorageNormalizationError(
            "FPS resampling provenance nominal rate does not match the HLS source"
        )
    allowed_duration_drift = max(
        profile.max_duration_drift_seconds,
        1.0 / expected.timeline.nominal_fps,
    )
    if (
        abs(expected.timeline.duration_seconds - source.timeline.duration_seconds)
        > allowed_duration_drift
    ):
        raise VideoStorageNormalizationError(
            "FPS resampling provenance duration does not match the HLS source"
        )
    if (
        expected.timeline.time_base_num is not None
        and source.timeline.time_base_num is not None
        and (
            expected.timeline.time_base_num != source.timeline.time_base_num
            or expected.timeline.time_base_den != source.timeline.time_base_den
        )
    ):
        raise VideoStorageNormalizationError(
            "FPS resampling provenance time base does not match the HLS source"
        )


def _boundary_key(
    boundary: PresentationTimestampBoundary,
) -> tuple[str, int, int]:
    return (
        boundary.coordinate_kind,
        boundary.reference_id,
        boundary.frame_number,
    )


def _assert_constant_cadence(
    timeline: PresentationTimestampTimeline,
    *,
    expected_cadence_seconds: float,
    tolerance_seconds: float,
    label: str,
) -> None:
    if (
        abs(timeline.minimum_cadence_seconds - expected_cadence_seconds)
        > tolerance_seconds
        or abs(timeline.maximum_cadence_seconds - expected_cadence_seconds)
        > tolerance_seconds
    ):
        raise VideoStorageNormalizationError(
            f"{label} presentation-timestamp cadence is not constant at 50 FPS"
        )


def validate_proven_resampled_hls_equivalence(
    *,
    source: VideoArtifactProbe,
    output: VideoArtifactProbe,
    source_pts: PresentationTimestampTimeline,
    output_pts: PresentationTimestampTimeline,
    context: ProvenResampledHlsContext,
    profile: VideoStorageProfile,
) -> None:
    """Accept the gc-10 rate shape only with complete temporal proof."""
    provenance = context.provenance
    if provenance is None:
        raise VideoStorageNormalizationError(
            "HLS measured-rate exception requires FPS resampling provenance"
        )
    if not context.source_generation_verified:
        raise VideoStorageNormalizationError(
            "HLS source generation does not match its persisted content hash"
        )
    _assert_provenance_matches_source(provenance, source, profile=profile)
    if not (
        math.isclose(source.timeline.nominal_fps, 50.0, abs_tol=0.001)
        and math.isclose(output.timeline.nominal_fps, 50.0, abs_tol=0.001)
        and math.isclose(provenance.max_fps, 50.0, abs_tol=0.001)
    ):
        raise VideoStorageNormalizationError(
            "HLS measured-rate exception is restricted to proven 50 FPS generations"
        )
    if (
        source_pts.frame_count != source.timeline.frame_count
        or output_pts.frame_count != output.timeline.frame_count
        or source_pts.frame_count != output_pts.frame_count
    ):
        raise VideoStorageNormalizationError(
            "Presentation-timestamp frame count does not match the probed timeline"
        )

    source_tick = _time_base_seconds(source.timeline)
    output_tick = _time_base_seconds(output.timeline)
    cadence_tolerance = max(source_tick, output_tick, 1e-6) * 1.5
    expected_cadence = 1.0 / 50.0
    _assert_constant_cadence(
        source_pts,
        expected_cadence_seconds=expected_cadence,
        tolerance_seconds=cadence_tolerance,
        label="Source",
    )
    _assert_constant_cadence(
        output_pts,
        expected_cadence_seconds=expected_cadence,
        tolerance_seconds=cadence_tolerance,
        label="Output",
    )
    source_span = source_pts.last_timestamp_seconds - source_pts.first_timestamp_seconds
    output_span = output_pts.last_timestamp_seconds - output_pts.first_timestamp_seconds
    if abs(source_span - output_span) > cadence_tolerance:
        raise VideoStorageNormalizationError(
            "HLS presentation-timestamp timeline span drifted"
        )

    persisted_boundaries = {
        _boundary_key(boundary): boundary for boundary in context.boundaries
    }
    source_boundaries = {
        _boundary_key(boundary): boundary for boundary in source_pts.boundaries
    }
    output_boundaries = {
        _boundary_key(boundary): boundary for boundary in output_pts.boundaries
    }
    expected_keys = {_boundary_key(boundary) for boundary in context.boundaries}
    if source_boundaries.keys() != expected_keys or output_boundaries.keys() != (
        expected_keys
    ):
        raise VideoStorageNormalizationError(
            "HLS presentation-timestamp boundary proof is incomplete"
        )
    for key in expected_keys:
        persisted_boundary = persisted_boundaries[key]
        source_boundary = source_boundaries[key]
        output_boundary = output_boundaries[key]
        source_relative = (
            source_boundary.timestamp_seconds - source_pts.first_timestamp_seconds
        )
        output_relative = (
            output_boundary.timestamp_seconds - output_pts.first_timestamp_seconds
        )
        if (
            abs(
                source_boundary.timestamp_seconds - persisted_boundary.timestamp_seconds
            )
            > cadence_tolerance
            or abs(source_relative - output_relative) > cadence_tolerance
        ):
            raise VideoStorageNormalizationError(
                "HLS presentation-timestamp segment or frame boundary drifted"
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
