from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Literal
from fractions import Fraction
from unittest.mock import patch

import pytest

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_files._time import (
    resolve_video_timeline_mapping,
    video_source_timeline,
)
from endoreg_db.services.video_files.timeline_index import load_video_timeline_index
from endoreg_db.services.video_files.timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
    seconds_to_frame_number,
    frame_durations,
    presentation_timestamp_fraction,
    presentation_timestamp_seconds,
    validate_presentation_timestamp_seconds,
    validate_display_frame_number,
)


@pytest.mark.parametrize("fps", [None, 25.0])
def test_missing_source_timeline_rejects_rate_based_coordinate_mapping(
    fps: float | None,
) -> None:
    video = VideoFile(pk=1, fps=fps, frame_count=250, duration=10.0)
    with patch.object(video, "get_fps", side_effect=AssertionError("must not probe")):
        with pytest.raises(
            VideoTimelineMappingError, match="requires a persisted source timeline"
        ):
            resolve_video_timeline_mapping(video)
        with pytest.raises(
            VideoTimelineMappingError, match="requires a persisted source timeline"
        ):
            load_video_timeline_index(video)


def test_optional_source_timeline_read_never_attempts_recovery() -> None:
    video = VideoFile(pk=1, meta={})
    with patch(
        "endoreg_db.services.video_files.timestamp_backfill.backfill_missing_video_timestamps",
        side_effect=AssertionError("read-only evidence must not recover media"),
    ):
        assert video_source_timeline(video) is None


@pytest.mark.parametrize(
    ("tick", "numerator", "denominator", "expected"),
    [
        (144_224, 1, 90_000, Fraction(9014, 5625)),
        (2000, 1, 1000, Fraction(2)),
        (7, 1001, 30_000, Fraction(7007, 30_000)),
    ],
)
def test_tick_conversion_keeps_exact_clock_arithmetic(
    tick: int, numerator: int, denominator: int, expected: Fraction
) -> None:
    assert presentation_timestamp_fraction(tick, numerator, denominator) == expected
    assert presentation_timestamp_seconds(tick, numerator, denominator) == float(
        expected
    )


@pytest.mark.parametrize(
    ("tick", "numerator", "denominator"),
    [
        (-1, 1, 1000),
        (True, 1, 1000),
        (1, True, 1000),
        (1, 0, 1000),
        (1, 1, 0),
        (1, 1, -1000),
    ],
)
def test_tick_conversion_rejects_invalid_identity_or_clock(
    tick: int, numerator: int, denominator: int
) -> None:
    with pytest.raises(VideoTimelineMappingError):
        presentation_timestamp_fraction(tick, numerator, denominator)


@pytest.mark.parametrize("seconds", [1.602488, 144_224 / 90_000])
def test_decimal_decoder_rounding_preserves_exact_tick_identity(seconds: float) -> None:
    assert (
        validate_presentation_timestamp_seconds(144_224, 1, 90_000, seconds) == seconds
    )


@pytest.mark.parametrize("seconds", [1.61, -1.0, math.nan, math.inf, True])
def test_decimal_seconds_cannot_reidentify_a_frame(seconds: float) -> None:
    with pytest.raises(VideoTimelineMappingError):
        validate_presentation_timestamp_seconds(144_224, 1, 90_000, seconds)


@pytest.mark.parametrize(
    ("frame_number", "frame_count"),
    [(True, 3), (-1, 3), (3, 3), (0, 0), (0, True)],
)
def test_display_frame_validation_rejects_terminal_and_invalid_counts(
    frame_number: int, frame_count: int
) -> None:
    with pytest.raises(VideoTimelineMappingError):
        validate_display_frame_number(frame_number, frame_count)


def test_partial_frame_durations_do_not_bridge_missing_or_invalid_timing() -> None:
    durations = frame_durations(
        {0: 2.0, 1: 2.04, 3: 2.2, 4: math.nan, 5: 2.4, 6: 2.4, 7: 2.5},
        terminal_frame_number=8,
        terminal_timestamp=2.7,
    )
    expected = {0: 0.04, 6: 0.1, 7: 0.2}
    assert durations.keys() == expected.keys()
    assert all(
        math.isclose(durations[number], value) for number, value in expected.items()
    )


@pytest.mark.parametrize(
    ("frame_number", "timestamp"), [(0, 1.99), (3, 2.2), (3, 2.3), (4, 2.1), (4, 2.3)]
)
def test_persisted_coordinates_respect_nonzero_origin_and_exclusive_terminal(
    frame_number: int, timestamp: float
) -> None:
    mapping = VideoTimelineMapping(
        fps=25,
        frame_count=4,
        duration_seconds=0.2,
        requires_persisted_pts=True,
        first_timestamp_seconds=2.0,
    )
    with pytest.raises(VideoTimelineMappingError):
        frame_number_to_seconds(mapping, frame_number, persisted_timestamp=timestamp)


@pytest.mark.parametrize("timestamp_mapping", ["rational_cfr", "ffprobe_pts"])
def test_explicit_source_timeline_controls_coordinate_mapping(
    timestamp_mapping: Literal["rational_cfr", "ffprobe_pts"],
) -> None:
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=VideoArtifactProbe(
            codec_name="h264",
            pixel_format="yuv420p",
            width=1280,
            height=720,
            bit_rate_bps=800_000,
            size_bytes=1_000_000,
            timeline=VideoTimelineContract(
                fps_num=25,
                fps_den=1,
                frame_count=250,
                duration_seconds=10.0,
                variable_frame_rate=timestamp_mapping == "ffprobe_pts",
                time_base_num=1,
                time_base_den=1000,
            ),
        ),
        artifact_kind="processed",
        content_hash="a" * 64,
        first_timestamp_seconds=2.0,
        timestamp_mapping=timestamp_mapping,
    )
    video = VideoFile(
        fps=50.0,
        processed_video_hash="a" * 64,
        meta={"source_timeline": evidence.model_dump(mode="json")},
    )
    mapping = resolve_video_timeline_mapping(video)
    assert mapping.fps == 25.0
    assert mapping.requires_persisted_pts == (timestamp_mapping == "ffprobe_pts")
    if timestamp_mapping == "rational_cfr":
        assert frame_number_to_seconds(mapping, 25, persisted_timestamp=None) == 3.0
    else:
        with pytest.raises(VideoTimelineMappingError, match="no persisted PTS"):
            frame_number_to_seconds(mapping, 25, persisted_timestamp=None)
        assert frame_number_to_seconds(mapping, 25, persisted_timestamp=3.1) == 3.1


def test_nonzero_origin_terminal_is_an_absolute_pts_boundary() -> None:
    mapping = VideoTimelineMapping(
        fps=25,
        frame_count=4,
        duration_seconds=0.16,
        requires_persisted_pts=True,
        first_timestamp_seconds=1.4,
    )
    terminal = frame_number_to_seconds(mapping, 4, persisted_timestamp=None)
    assert abs(terminal - 1.56) < 1e-12
    assert seconds_to_frame_number(mapping, 1.56, neighboring_timestamps={3: 1.52}) == 4


@pytest.mark.parametrize(
    ("frame_number", "persisted_timestamp", "expected"),
    [(25, None, 1.0), (25, 1.017, 1.017), (250, None, 10.0)],
)
def test_frame_number_to_seconds_uses_one_canonical_precedence_rule(
    frame_number: int,
    persisted_timestamp: float | None,
    expected: float,
) -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=250,
        duration_seconds=10.0,
        requires_persisted_pts=False,
    )

    assert (
        frame_number_to_seconds(
            mapping,
            frame_number,
            persisted_timestamp=persisted_timestamp,
        )
        == expected
    )


def test_vfr_frame_without_persisted_pts_fails_closed() -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=250,
        duration_seconds=10.0,
        requires_persisted_pts=True,
    )

    with pytest.raises(VideoTimelineMappingError, match="no persisted PTS"):
        frame_number_to_seconds(mapping, 25, persisted_timestamp=None)


def test_seconds_to_frame_number_prefers_nearest_pts_and_lower_tie() -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=250,
        duration_seconds=10.0,
        requires_persisted_pts=True,
    )

    assert (
        seconds_to_frame_number(
            mapping,
            1.05,
            neighboring_timestamps={25: 1.0, 26: 1.1},
        )
        == 25
    )


def test_cfr_timestamp_without_pts_uses_rate_instead_of_video_endpoint() -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=250,
        duration_seconds=10.0,
        requires_persisted_pts=False,
    )

    assert (
        seconds_to_frame_number(
            mapping,
            1.0,
            neighboring_timestamps={},
        )
        == 25
    )


def test_vfr_video_endpoint_does_not_require_a_persisted_frame_row() -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=250,
        duration_seconds=10.0,
        requires_persisted_pts=True,
    )

    assert (
        seconds_to_frame_number(
            mapping,
            10.0,
            neighboring_timestamps={},
        )
        == 250
    )


@pytest.mark.parametrize("timestamp", [-0.1, math.inf, math.nan])
def test_seconds_to_frame_number_rejects_invalid_timestamps(timestamp: float) -> None:
    mapping = VideoTimelineMapping(
        fps=25.0,
        frame_count=None,
        duration_seconds=None,
        requires_persisted_pts=False,
    )

    with pytest.raises(VideoTimelineMappingError, match="finite and non-negative"):
        seconds_to_frame_number(
            mapping,
            timestamp,
            neighboring_timestamps={},
        )
