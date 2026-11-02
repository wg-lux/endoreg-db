from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Literal
from unittest.mock import patch

import pytest

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_files._time import resolve_video_timeline_mapping
from endoreg_db.services.video_files.timeline_index import load_video_timeline_index
from endoreg_db.services.video_files.timeline import (
    VideoTimelineMapping,
    VideoTimelineMappingError,
    frame_number_to_seconds,
    seconds_to_frame_number,
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
