from __future__ import annotations

import pytest
from lx_dtypes.models.contracts.video_provenance import (
    VideoFrameProvenancePayload,
    VideoFrameTimeline,
    VideoProvenanceBundle,
    VideoProvenanceRecovery,
)

from endoreg_db.services.video_files.segment_coordinates import (
    RecordingMappingStatus,
    SegmentCoordinateError,
    SegmentFrameRange,
    SegmentGenerationCoordinates,
    SegmentRecordingCoordinates,
    SegmentTimelineCoordinates,
    segment_timeline_coordinates,
    wire_timeline_coordinates,
)

RECORDING_HASH = "a" * 64
MASKED_HASH = "b" * 64
PROCESSED_HASH = "c" * 64


def _timeline(content_hash: str, ticks: list[int], *, den: int) -> VideoFrameTimeline:
    return VideoFrameTimeline(
        content_hash=content_hash,
        time_base_num=1,
        time_base_den=den,
        presentation_timestamps=ticks,
    )


def _two_step_bundle() -> VideoProvenanceBundle:
    """Recording (6 frames) -> drop frames 1 and 4 -> repeat the second output."""
    recording = _timeline(
        RECORDING_HASH, [90000, 93003, 96006, 99009, 102012, 105015], den=90000
    )
    masked = _timeline(MASKED_HASH, [1000, 1067, 1100, 1167], den=1000)
    processed = _timeline(PROCESSED_HASH, [0, 20, 40, 60, 80], den=1000)
    return VideoProvenanceBundle(
        transformations=[
            VideoFrameProvenancePayload(
                source=recording, output=masked, source_frame_indices=[0, 2, 3, 5]
            ),
            VideoFrameProvenancePayload(
                source=masked, output=processed, source_frame_indices=[0, 1, 1, 2, 3]
            ),
        ]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start", "end", "recording_first", "recording_last"),
    [
        (0, 5, 0, 5),
        (1, 3, 2, 2),
        (2, 4, 2, 3),
        (4, 5, 5, 5),
    ],
)
def test_verified_bundle_binds_generation_and_recording_ticks(
    start: int, end: int, recording_first: int, recording_last: int
) -> None:
    bundle = _two_step_bundle()
    processed_ticks = bundle.transformations[-1].output.presentation_timestamps
    recording_ticks = bundle.transformations[0].source.presentation_timestamps

    [coordinates] = segment_timeline_coordinates(
        bundle,
        processed_content_hash=PROCESSED_HASH,
        segments=[SegmentFrameRange(9, start, end)],
    )

    assert coordinates == SegmentTimelineCoordinates(
        segment_id=9,
        generation=SegmentGenerationCoordinates(
            content_hash=PROCESSED_HASH,
            time_base_num=1,
            time_base_den=1000,
            first_frame_number=start,
            last_frame_number=end - 1,
            first_presentation_timestamp=processed_ticks[start],
            last_presentation_timestamp=processed_ticks[end - 1],
        ),
        recording_mapping_status=RecordingMappingStatus.VERIFIED,
        recording=SegmentRecordingCoordinates(
            recording_content_hash=RECORDING_HASH,
            time_base_num=1,
            time_base_den=90000,
            first_frame_index=recording_first,
            last_frame_index=recording_last,
            first_presentation_timestamp=recording_ticks[recording_first],
            last_presentation_timestamp=recording_ticks[recording_last],
        ),
    )


@pytest.mark.unit
def test_unresolved_receipt_never_reports_original_frames() -> None:
    processed = _timeline(PROCESSED_HASH, [0, 40, 80], den=1000)
    recovery = VideoProvenanceRecovery(
        recording_hash=RECORDING_HASH,
        original_timeline=_timeline(RECORDING_HASH, [0, 33, 66], den=1000),
        processed_timeline=processed,
    )

    [coordinates] = segment_timeline_coordinates(
        recovery,
        processed_content_hash=PROCESSED_HASH,
        segments=[SegmentFrameRange(3, 1, 3)],
    )

    assert coordinates.recording_mapping_status == RecordingMappingStatus.UNRESOLVED
    assert coordinates.recording is None
    assert coordinates.generation.first_presentation_timestamp == 40
    assert coordinates.generation.last_presentation_timestamp == 80


@pytest.mark.unit
@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (-1, 2, "0 <= start < exclusive end"),
        (2, 2, "0 <= start < exclusive end"),
        (3, 1, "0 <= start < exclusive end"),
        (0, 6, "exceeds the processed timeline"),
    ],
)
def test_invalid_segment_ranges_fail(start: int, end: int, message: str) -> None:
    with pytest.raises(SegmentCoordinateError, match=message):
        segment_timeline_coordinates(
            _two_step_bundle(),
            processed_content_hash=PROCESSED_HASH,
            segments=[SegmentFrameRange(1, start, end)],
        )


@pytest.mark.unit
def test_provenance_for_another_generation_fails() -> None:
    with pytest.raises(SegmentCoordinateError, match="published processed generation"):
        segment_timeline_coordinates(
            _two_step_bundle(),
            processed_content_hash="d" * 64,
            segments=[SegmentFrameRange(1, 0, 1)],
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("status", "with_recording"),
    [
        (RecordingMappingStatus.VERIFIED, False),
        (RecordingMappingStatus.UNRESOLVED, True),
    ],
)
def test_recording_coordinates_follow_mapping_status(
    status: RecordingMappingStatus, with_recording: bool
) -> None:
    generation = SegmentGenerationCoordinates(PROCESSED_HASH, 1, 1000, 0, 0, 0, 0)
    recording = SegmentRecordingCoordinates(RECORDING_HASH, 1, 1000, 0, 0, 0, 0)
    with pytest.raises(SegmentCoordinateError, match="exactly for verified ancestry"):
        SegmentTimelineCoordinates(
            segment_id=1,
            generation=generation,
            recording_mapping_status=status,
            recording=recording if with_recording else None,
        )


@pytest.mark.unit
@pytest.mark.parametrize("verified", [True, False], ids=["verified", "unresolved"])
def test_wire_coordinates_round_trip_through_shared_contract(verified: bool) -> None:
    from lx_dtypes.models.contracts.video_segment_timeline import (
        VideoSegmentTimelineCoordinates,
    )

    bundle = _two_step_bundle()
    provenance: VideoProvenanceBundle | VideoProvenanceRecovery = (
        bundle
        if verified
        else VideoProvenanceRecovery(
            recording_hash=RECORDING_HASH,
            processed_timeline=bundle.transformations[-1].output,
        )
    )
    [coordinates] = segment_timeline_coordinates(
        provenance,
        processed_content_hash=PROCESSED_HASH,
        segments=[SegmentFrameRange(1, 1, 4)],
    )

    wire = wire_timeline_coordinates(coordinates)

    assert wire.recording_mapping_status == coordinates.recording_mapping_status.value
    assert wire.generation.model_dump() == {
        "content_hash": PROCESSED_HASH,
        "time_base_num": 1,
        "time_base_den": 1000,
        "first_frame_number": 1,
        "last_frame_number": 3,
        "first_presentation_timestamp": 20,
        "last_presentation_timestamp": 60,
    }
    assert (wire.recording is not None) is verified
    if wire.recording is not None:
        assert (wire.recording.first_frame_index, wire.recording.last_frame_index) == (
            2,
            3,
        )
    assert (
        VideoSegmentTimelineCoordinates.model_validate_json(wire.model_dump_json())
        == wire
    )
