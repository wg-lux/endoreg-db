from __future__ import annotations

from dataclasses import dataclass

import pytest

from endoreg_db.services.video_storage_normalization import (
    VideoStorageNormalizationError,
    segment_timeline_references,
    timeline_from_video_metadata,
)


@dataclass(frozen=True)
class _Segment:
    pk: int
    start_frame_number: int
    end_frame_number: int


class _SegmentValues:
    def __init__(self, segments: list[_Segment]) -> None:
        self._segments = segments

    def order_by(self, field: str) -> "_SegmentValues":
        assert field == "pk"
        return self

    def values_list(self, *fields: str) -> list[tuple[int, int, int]]:
        assert fields == ("pk", "start_frame_number", "end_frame_number")
        return [
            (segment.pk, segment.start_frame_number, segment.end_frame_number)
            for segment in self._segments
        ]


class _FrameValues:
    def __init__(self, timestamps: dict[int, float]) -> None:
        self._timestamps = timestamps

    def filter(self, **kwargs: object) -> "_FrameValues":
        requested = kwargs["frame_number__in"]
        assert isinstance(requested, set)
        assert kwargs["timestamp__isnull"] is False
        return _FrameValues(
            {key: value for key, value in self._timestamps.items() if key in requested}
        )

    def values_list(self, *fields: str) -> list[tuple[int, float]]:
        assert fields == ("frame_number", "timestamp")
        return list(self._timestamps.items())


@dataclass
class _Video:
    label_video_segments: _SegmentValues
    frames: _FrameValues | None = None


@pytest.mark.unit
def test_cfr_segment_timestamps_fall_back_to_rational_fps_without_pts() -> None:
    timeline = timeline_from_video_metadata(
        fps=25.0,
        duration_seconds=10.0,
        frame_count=250,
    )
    video = _Video(
        label_video_segments=_SegmentValues(
            [_Segment(pk=7, start_frame_number=25, end_frame_number=75)]
        )
    )

    references = segment_timeline_references(video, timeline=timeline)

    assert len(references) == 1
    assert references[0].segment_id == 7
    assert references[0].start_timestamp_seconds == 1.0
    assert references[0].end_timestamp_seconds == 3.0
    assert references[0].timeline_version == "pts_v1"


@pytest.mark.unit
def test_vfr_segment_timestamps_use_persisted_pts() -> None:
    timeline = timeline_from_video_metadata(
        fps=25.0,
        duration_seconds=10.0,
        frame_count=250,
    ).model_copy(
        update={
            "variable_frame_rate": True,
            "time_base_num": 1,
            "time_base_den": 90_000,
        }
    )
    video = _Video(
        label_video_segments=_SegmentValues(
            [_Segment(pk=8, start_frame_number=25, end_frame_number=75)]
        ),
        frames=_FrameValues({25: 1.017, 75: 3.041}),
    )

    references = segment_timeline_references(video, timeline=timeline)

    assert references[0].start_timestamp_seconds == 1.017
    assert references[0].end_timestamp_seconds == 3.041
    assert references[0].start_timestamp_seconds != 25 / timeline.fps
    assert references[0].end_timestamp_seconds != 75 / timeline.fps


@pytest.mark.unit
def test_vfr_segment_mapping_fails_closed_when_boundary_pts_is_missing() -> None:
    timeline = timeline_from_video_metadata(
        fps=25.0,
        duration_seconds=10.0,
        frame_count=250,
    ).model_copy(
        update={
            "variable_frame_rate": True,
            "time_base_num": 1,
            "time_base_den": 90_000,
        }
    )
    video = _Video(
        label_video_segments=_SegmentValues(
            [_Segment(pk=9, start_frame_number=25, end_frame_number=75)]
        ),
        frames=_FrameValues({25: 1.017}),
    )

    with pytest.raises(
        VideoStorageNormalizationError,
        match=r"persisted PTS.*missing frames: \[75\]",
    ):
        segment_timeline_references(video, timeline=timeline)


@pytest.mark.unit
def test_cfr_segment_mapping_prefers_persisted_pts_when_available() -> None:
    timeline = timeline_from_video_metadata(
        fps=25.0,
        duration_seconds=10.0,
        frame_count=250,
    )
    video = _Video(
        label_video_segments=_SegmentValues(
            [_Segment(pk=7, start_frame_number=25, end_frame_number=75)]
        ),
        frames=_FrameValues({25: 1.01, 75: 3.01}),
    )

    references = segment_timeline_references(video, timeline=timeline)

    assert references[0].start_timestamp_seconds == 1.01
    assert references[0].end_timestamp_seconds == 3.01
