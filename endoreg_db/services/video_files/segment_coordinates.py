"""Exact segment coordinates on the processed generation and original recording.

Transfer and export boundaries convert these results into the shared lx-dtypes
wire contract. Original-recording coordinates come only from composed decoder
ancestry (``source_frame_indices``); nearest-timestamp matching is never used.
An operator-acknowledged unresolved receipt yields an explicit unresolved
status without original values.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from lx_dtypes.models.contracts.video_provenance import (
    VideoFrameTimeline,
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)
from lx_dtypes.models.contracts.video_segment_timeline import (
    VideoSegmentGenerationCoordinates,
    VideoSegmentRecordingCoordinates,
    VideoSegmentTimelineCoordinates,
)

from endoreg_db.utils.rust_backend import compose_frame_ancestry


class SegmentCoordinateError(ValueError):
    """Raised when segment coordinates cannot be bound to exact frame identities."""


class RecordingMappingStatus(StrEnum):
    VERIFIED = "verified"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class SegmentFrameRange:
    """Persisted segment boundaries; the end frame number is exclusive."""

    segment_id: int
    start_frame_number: int
    end_frame_number_exclusive: int


@dataclass(frozen=True, slots=True)
class SegmentGenerationCoordinates:
    """First and last included frame on the current processed generation."""

    content_hash: str
    time_base_num: int
    time_base_den: int
    first_frame_number: int
    last_frame_number: int
    first_presentation_timestamp: int
    last_presentation_timestamp: int


@dataclass(frozen=True, slots=True)
class SegmentRecordingCoordinates:
    """Original-recording frames that produced the first and last included frame."""

    recording_content_hash: str
    time_base_num: int
    time_base_den: int
    first_frame_index: int
    last_frame_index: int
    first_presentation_timestamp: int
    last_presentation_timestamp: int


@dataclass(frozen=True, slots=True)
class SegmentTimelineCoordinates:
    segment_id: int
    generation: SegmentGenerationCoordinates
    recording_mapping_status: RecordingMappingStatus
    recording: SegmentRecordingCoordinates | None

    def __post_init__(self) -> None:
        verified = self.recording_mapping_status == RecordingMappingStatus.VERIFIED
        if verified != (self.recording is not None):
            raise SegmentCoordinateError(
                "Recording coordinates are required exactly for verified ancestry."
            )


def _validated_range(
    segment: SegmentFrameRange, *, frame_count: int
) -> tuple[int, int]:
    start = segment.start_frame_number
    end = segment.end_frame_number_exclusive
    if start < 0 or end <= start:
        raise SegmentCoordinateError(
            f"Segment {segment.segment_id} requires 0 <= start < exclusive end."
        )
    if end > frame_count:
        raise SegmentCoordinateError(
            f"Segment {segment.segment_id} exceeds the processed timeline "
            f"of {frame_count} frames."
        )
    return start, end - 1


def _generation_coordinates(
    timeline: VideoFrameTimeline, *, first: int, last: int
) -> SegmentGenerationCoordinates:
    ticks = timeline.presentation_timestamps
    return SegmentGenerationCoordinates(
        content_hash=timeline.content_hash,
        time_base_num=timeline.time_base_num,
        time_base_den=timeline.time_base_den,
        first_frame_number=first,
        last_frame_number=last,
        first_presentation_timestamp=ticks[first],
        last_presentation_timestamp=ticks[last],
    )


def _recording_coordinates(
    recording: VideoFrameTimeline,
    ancestry: Sequence[int],
    *,
    first: int,
    last: int,
) -> SegmentRecordingCoordinates:
    first_index = ancestry[first]
    last_index = ancestry[last]
    ticks = recording.presentation_timestamps
    return SegmentRecordingCoordinates(
        recording_content_hash=recording.content_hash,
        time_base_num=recording.time_base_num,
        time_base_den=recording.time_base_den,
        first_frame_index=first_index,
        last_frame_index=last_index,
        first_presentation_timestamp=ticks[first_index],
        last_presentation_timestamp=ticks[last_index],
    )


def _bundle_ancestry(bundle: VideoProvenanceBundle) -> list[int]:
    transformations = bundle.transformations
    try:
        ancestry = compose_frame_ancestry(
            [len(step.source.presentation_timestamps) for step in transformations],
            [list(step.source_frame_indices) for step in transformations],
        )
    except ValueError as exc:
        raise SegmentCoordinateError(str(exc)) from exc
    output_count = len(transformations[-1].output.presentation_timestamps)
    if len(ancestry) != output_count:
        raise SegmentCoordinateError(
            "Composed ancestry does not cover every processed frame."
        )
    return ancestry


def segment_timeline_coordinates(
    provenance: VideoProvenanceBundle | VideoProvenanceRecovery,
    *,
    processed_content_hash: str,
    segments: Sequence[SegmentFrameRange],
) -> list[SegmentTimelineCoordinates]:
    """Bind persisted segment frames to exact generation and recording ticks."""
    if isinstance(provenance, VideoProvenanceBundle):
        output = provenance.transformations[-1].output
        recording: VideoFrameTimeline | None = provenance.transformations[0].source
        ancestry: list[int] | None = _bundle_ancestry(provenance)
        status = RecordingMappingStatus.VERIFIED
    else:
        output = provenance.processed_timeline
        recording = None
        ancestry = None
        status = RecordingMappingStatus.UNRESOLVED
    if output.content_hash != processed_content_hash:
        raise SegmentCoordinateError(
            "Provenance does not describe the published processed generation."
        )

    frame_count = len(output.presentation_timestamps)
    coordinates: list[SegmentTimelineCoordinates] = []
    for segment in segments:
        first, last = _validated_range(segment, frame_count=frame_count)
        coordinates.append(
            SegmentTimelineCoordinates(
                segment_id=segment.segment_id,
                generation=_generation_coordinates(output, first=first, last=last),
                recording_mapping_status=status,
                recording=(
                    None
                    if recording is None or ancestry is None
                    else _recording_coordinates(
                        recording, ancestry, first=first, last=last
                    )
                ),
            )
        )
    return coordinates


def wire_timeline_coordinates(
    coordinates: SegmentTimelineCoordinates,
) -> VideoSegmentTimelineCoordinates:
    """Convert service coordinates into the shared transfer and export contract."""
    generation = coordinates.generation
    recording = coordinates.recording
    return VideoSegmentTimelineCoordinates(
        generation=VideoSegmentGenerationCoordinates(
            content_hash=generation.content_hash,
            time_base_num=generation.time_base_num,
            time_base_den=generation.time_base_den,
            first_frame_number=generation.first_frame_number,
            last_frame_number=generation.last_frame_number,
            first_presentation_timestamp=generation.first_presentation_timestamp,
            last_presentation_timestamp=generation.last_presentation_timestamp,
        ),
        recording_mapping_status=coordinates.recording_mapping_status.value,
        recording=(
            None
            if recording is None
            else VideoSegmentRecordingCoordinates(
                recording_content_hash=recording.recording_content_hash,
                time_base_num=recording.time_base_num,
                time_base_den=recording.time_base_den,
                first_frame_index=recording.first_frame_index,
                last_frame_index=recording.last_frame_index,
                first_presentation_timestamp=recording.first_presentation_timestamp,
                last_presentation_timestamp=recording.last_presentation_timestamp,
            )
        ),
    )


__all__ = [
    "RecordingMappingStatus",
    "SegmentCoordinateError",
    "SegmentFrameRange",
    "SegmentGenerationCoordinates",
    "SegmentRecordingCoordinates",
    "SegmentTimelineCoordinates",
    "segment_timeline_coordinates",
    "wire_timeline_coordinates",
]
