from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping


class VideoTimelineMappingError(ValueError):
    """Raised when frame and time coordinates cannot be mapped safely."""


@dataclass(frozen=True, slots=True)
class VideoTimelineMapping:
    """Metadata required for a frame/time conversion.

    Persisted presentation timestamps are supplied per operation so callers can
    load either one boundary, two neighboring boundaries, or a bulk set without
    forcing a particular persistence strategy into the conversion rules.
    """

    fps: float | None
    frame_count: int | None
    duration_seconds: float | None
    requires_persisted_pts: bool

    def __post_init__(self) -> None:
        if self.fps is not None and (not math.isfinite(self.fps) or self.fps <= 0):
            raise VideoTimelineMappingError(
                "FPS must be set, finite, and greater than zero."
            )
        if self.frame_count is not None and self.frame_count < 0:
            raise VideoTimelineMappingError("Frame count must be non-negative.")
        if self.duration_seconds is not None and (
            not math.isfinite(self.duration_seconds) or self.duration_seconds < 0
        ):
            raise VideoTimelineMappingError("Duration must be finite and non-negative.")


def frame_number_to_seconds(
    mapping: VideoTimelineMapping,
    frame_number: int,
    *,
    persisted_timestamp: float | None,
) -> float:
    """Resolve a frame boundary, preferring its persisted presentation time."""
    validate_frame_number(mapping, frame_number)

    if persisted_timestamp is not None:
        timestamp = _validated_timestamp(
            persisted_timestamp,
            field_name=f"Persisted PTS for frame {frame_number}",
        )
        return timestamp

    if (
        mapping.frame_count is not None
        and frame_number == mapping.frame_count
        and mapping.duration_seconds is not None
        and mapping.duration_seconds > 0
    ):
        return mapping.duration_seconds

    if mapping.requires_persisted_pts:
        raise VideoTimelineMappingError(
            f"Frame {frame_number} has no persisted PTS for a VFR timeline."
        )
    if mapping.fps is None:
        raise VideoTimelineMappingError("FPS must be set and greater than zero.")
    return frame_number / mapping.fps


def seconds_to_frame_number(
    mapping: VideoTimelineMapping,
    timestamp_seconds: float,
    *,
    neighboring_timestamps: Mapping[int, float],
) -> int:
    """Resolve a timestamp to its nearest known or calculated frame boundary.

    Equidistant persisted boundaries select the lower frame number. CFR rounding
    follows the same half-up rule when no persisted boundary is available.
    """
    timestamp = validate_timestamp_seconds(timestamp_seconds)
    duration = mapping.duration_seconds
    if (
        duration is not None
        and timestamp > duration
        and not math.isclose(
            timestamp,
            duration,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    ):
        raise VideoTimelineMappingError(
            f"Timestamp {timestamp:g}s exceeds video duration {duration:g}s."
        )

    candidates = _validated_candidates(mapping, neighboring_timestamps)
    if candidates:
        if mapping.frame_count is not None and duration is not None:
            candidates[mapping.frame_count] = duration
        return min(
            candidates,
            key=lambda number: (abs(candidates[number] - timestamp), number),
        )

    if (
        mapping.frame_count is not None
        and duration is not None
        and math.isclose(timestamp, duration, rel_tol=0.0, abs_tol=1e-9)
    ):
        return mapping.frame_count

    if mapping.requires_persisted_pts:
        raise VideoTimelineMappingError(
            "VFR timestamp conversion requires persisted frame PTS."
        )
    if mapping.fps is None:
        raise VideoTimelineMappingError("FPS must be set and greater than zero.")
    frame_number = math.floor(timestamp * mapping.fps + 0.5)
    if mapping.frame_count is not None:
        return min(frame_number, mapping.frame_count)
    return frame_number


def validate_frame_number(mapping: VideoTimelineMapping, frame_number: int) -> None:
    """Validate a frame coordinate before a caller performs persistence I/O."""
    if frame_number < 0:
        raise VideoTimelineMappingError("Frame number must be non-negative.")
    if mapping.frame_count is not None and frame_number > mapping.frame_count:
        raise VideoTimelineMappingError(
            f"Frame number {frame_number} exceeds frame count {mapping.frame_count}."
        )


def validate_timestamp_seconds(timestamp_seconds: float) -> float:
    """Normalize and validate a timestamp before persistence I/O."""
    return _validated_timestamp(timestamp_seconds, field_name="Timestamp")


def _validated_candidates(
    mapping: VideoTimelineMapping,
    timestamps: Mapping[int, float],
) -> dict[int, float]:
    candidates: dict[int, float] = {}
    for frame_number, raw_timestamp in timestamps.items():
        if frame_number < 0:
            raise VideoTimelineMappingError(
                "Persisted PTS frame numbers must be non-negative."
            )
        if mapping.frame_count is not None and frame_number > mapping.frame_count:
            raise VideoTimelineMappingError(
                f"Persisted PTS frame {frame_number} exceeds frame count "
                f"{mapping.frame_count}."
            )
        candidates[frame_number] = _validated_timestamp(
            raw_timestamp,
            field_name=f"Persisted PTS for frame {frame_number}",
        )
    return candidates


def _validated_timestamp(value: float, *, field_name: str) -> float:
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise VideoTimelineMappingError(
            f"{field_name} must be finite and non-negative."
        )
    return timestamp


__all__ = [
    "VideoTimelineMapping",
    "VideoTimelineMappingError",
    "frame_number_to_seconds",
    "seconds_to_frame_number",
    "validate_frame_number",
    "validate_timestamp_seconds",
]
