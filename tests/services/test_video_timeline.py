from __future__ import annotations

import math

import pytest

from endoreg_db.services.video_timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
    seconds_to_frame_number,
)


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
