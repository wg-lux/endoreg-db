"""Compatibility imports for the shared, initialization-free timeline rules."""

from endoreg_db.utils.video.timeline import (
    VideoTimelineMapping as VideoTimelineMapping,
    VideoTimelineMappingError as VideoTimelineMappingError,
    frame_durations as frame_durations,
    frame_number_to_seconds as frame_number_to_seconds,
    presentation_timestamp_fraction as presentation_timestamp_fraction,
    presentation_timestamp_seconds as presentation_timestamp_seconds,
    seconds_to_frame_number as seconds_to_frame_number,
    validate_display_frame_number as validate_display_frame_number,
    validate_frame_number as validate_frame_number,
    validate_persisted_frame_timestamp as validate_persisted_frame_timestamp,
    validate_presentation_timestamp_seconds as validate_presentation_timestamp_seconds,
    validate_stream_time_base as validate_stream_time_base,
    validate_timestamp_seconds as validate_timestamp_seconds,
)

__all__ = [
    "VideoTimelineMapping",
    "VideoTimelineMappingError",
    "frame_durations",
    "frame_number_to_seconds",
    "presentation_timestamp_fraction",
    "presentation_timestamp_seconds",
    "seconds_to_frame_number",
    "validate_display_frame_number",
    "validate_frame_number",
    "validate_persisted_frame_timestamp",
    "validate_presentation_timestamp_seconds",
    "validate_stream_time_base",
    "validate_timestamp_seconds",
]
