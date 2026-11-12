"""Pure canonical frame/time rules shared by Django and standalone media tools."""

from __future__ import annotations

import math
from dataclasses import dataclass
from collections.abc import Mapping
from fractions import Fraction


class VideoTimelineMappingError(ValueError):
    """Raised when frame and time coordinates cannot be mapped safely."""


def validate_stream_time_base(time_base_num: int, time_base_den: int) -> None:
    """Require the exact positive integer clock shared by persisted frame ticks."""
    if any(
        type(value) is not int or value <= 0 for value in (time_base_num, time_base_den)
    ):
        raise VideoTimelineMappingError(
            "Stream time base must contain positive integers."
        )


def presentation_timestamp_fraction(
    tick: int, time_base_num: int, time_base_den: int
) -> Fraction:
    """Project a presentation timestamp without rounding before clock arithmetic."""
    validate_stream_time_base(time_base_num, time_base_den)
    if type(tick) is not int or tick < 0:
        raise VideoTimelineMappingError(
            "Presentation timestamp must be a non-negative integer."
        )
    return Fraction(tick * time_base_num, time_base_den)


def presentation_timestamp_seconds(
    tick: int, time_base_num: int, time_base_den: int
) -> float:
    """Convert exact stream ticks to validated presentation seconds at the edge."""
    try:
        seconds = float(
            presentation_timestamp_fraction(tick, time_base_num, time_base_den)
        )
    except OverflowError as exc:
        raise VideoTimelineMappingError(
            "Presentation timestamp exceeds finite seconds."
        ) from exc
    return validate_timestamp_seconds(seconds)


def validate_presentation_timestamp_seconds(
    tick: int, time_base_num: int, time_base_den: int, seconds: float
) -> float:
    """Check persisted decimal seconds against the exact frame's stream clock.

    Decoder seconds may be rounded to microseconds; that serialization tolerance
    does not select another frame or establish transformation ancestry.
    """
    actual = validate_timestamp_seconds(seconds)
    expected = presentation_timestamp_seconds(tick, time_base_num, time_base_den)
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-6):
        raise VideoTimelineMappingError(
            "Frame seconds disagree with its presentation timestamp."
        )
    return actual


def frame_durations(
    timestamps: Mapping[int, float],
    *,
    terminal_frame_number: int | None = None,
    terminal_timestamp: float | None = None,
) -> dict[int, float]:
    """Return observed positive adjacent-frame durations; missing timing stays absent."""
    boundaries = dict(timestamps)
    if terminal_frame_number is not None and terminal_timestamp is not None:
        boundaries[terminal_frame_number] = terminal_timestamp
    return {
        number: boundaries[number + 1] - timestamp
        for number, timestamp in timestamps.items()
        if number >= 0
        and number + 1 in boundaries
        and math.isfinite(timestamp)
        and timestamp >= 0
        and math.isfinite(boundaries[number + 1])
        and boundaries[number + 1] > timestamp
    }


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
    first_timestamp_seconds: float = 0.0

    @property
    def end_timestamp_seconds(self) -> float | None:
        return (
            None
            if self.duration_seconds is None
            else self.first_timestamp_seconds + self.duration_seconds
        )

    def __post_init__(self) -> None:
        _validated_timestamp(self.first_timestamp_seconds, field_name="Timeline origin")
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
        return validate_persisted_frame_timestamp(
            mapping, frame_number, persisted_timestamp
        )

    if (
        mapping.frame_count is not None
        and frame_number == mapping.frame_count
        and mapping.duration_seconds is not None
        and mapping.duration_seconds > 0
    ):
        return mapping.first_timestamp_seconds + mapping.duration_seconds

    if mapping.requires_persisted_pts:
        raise VideoTimelineMappingError(
            f"Frame {frame_number} has no persisted PTS for a VFR timeline."
        )
    if mapping.fps is None:
        raise VideoTimelineMappingError("FPS must be set and greater than zero.")
    return mapping.first_timestamp_seconds + frame_number / mapping.fps


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
    duration = mapping.end_timestamp_seconds
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
    frame_number = max(
        0, math.floor((timestamp - mapping.first_timestamp_seconds) * mapping.fps + 0.5)
    )
    if mapping.frame_count is not None:
        return min(frame_number, mapping.frame_count)
    return frame_number


def validate_frame_number(mapping: VideoTimelineMapping, frame_number: int) -> None:
    """Validate a frame coordinate before a caller performs persistence I/O."""
    if type(frame_number) is not int or frame_number < 0:
        raise VideoTimelineMappingError("Frame number must be non-negative.")
    if mapping.frame_count is not None and frame_number > mapping.frame_count:
        raise VideoTimelineMappingError(
            f"Frame number {frame_number} exceeds frame count {mapping.frame_count}."
        )


def validate_display_frame_number(frame_number: int, frame_count: int) -> None:
    """Require a decodable ordinal; the exclusive terminal is never an image."""
    if type(frame_count) is not int or frame_count <= 0:
        raise VideoTimelineMappingError(
            "Display frame count must be a positive integer."
        )
    if type(frame_number) is not int or not 0 <= frame_number < frame_count:
        raise VideoTimelineMappingError(
            "Display frame number must be an integer within the frame count."
        )


def validate_persisted_frame_timestamp(
    mapping: VideoTimelineMapping, frame_number: int, timestamp_seconds: float
) -> float:
    """Enforce the same display/terminal bounds as the native timeline index."""
    validate_frame_number(mapping, frame_number)
    timestamp = validate_timestamp_seconds(timestamp_seconds)
    if timestamp < mapping.first_timestamp_seconds:
        raise VideoTimelineMappingError(
            "Persisted frame timestamp precedes the timeline origin."
        )
    terminal = mapping.end_timestamp_seconds
    if terminal is not None and mapping.duration_seconds:
        if frame_number == mapping.frame_count:
            if not math.isclose(timestamp, terminal, rel_tol=0.0, abs_tol=1e-9):
                raise VideoTimelineMappingError(
                    "Persisted terminal timestamp disagrees with the timeline end."
                )
        elif timestamp >= terminal:
            raise VideoTimelineMappingError(
                "Persisted display timestamp must precede the timeline end."
            )
    return timestamp


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
        candidates[frame_number] = validate_persisted_frame_timestamp(
            mapping, frame_number, raw_timestamp
        )
    ordered = sorted(candidates)
    if any(
        candidates[right] <= candidates[left]
        for left, right in zip(ordered, ordered[1:])
    ):
        raise VideoTimelineMappingError(
            "Persisted frame timestamps must be strictly increasing."
        )
    return candidates


def _validated_timestamp(value: float, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise VideoTimelineMappingError(
            f"{field_name} must be finite and non-negative."
        )
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
    "validate_display_frame_number",
    "validate_timestamp_seconds",
    "validate_stream_time_base",
    "presentation_timestamp_fraction",
    "presentation_timestamp_seconds",
    "validate_presentation_timestamp_seconds",
    "validate_persisted_frame_timestamp",
    "frame_durations",
]
