from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import cast

import pytest

from endoreg_db.models import Center, Frame, LabelVideoSegment, VideoFile
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.serializers.label_video_segment.label_video_segment import (
    LabelVideoSegmentSerializer,
)
from endoreg_db.services.video_files import (
    get_video_frame_neighborhood,
    video_frame_number_to_seconds,
    video_seconds_to_frame_number,
)
from endoreg_db.services.video_files import frames as frame_service
from endoreg_db.services.video_storage_normalization import evidence_as_json

pytestmark = pytest.mark.django_db


def _vfr_probe() -> VideoArtifactProbe:
    return VideoArtifactProbe(
        codec_name="h264",
        pixel_format="yuv420p",
        width=1280,
        height=720,
        bit_rate_bps=800_000,
        size_bytes=1_000_000,
        timeline=VideoTimelineContract(
            fps_num=25,
            fps_den=1,
            duration_seconds=0.3,
            frame_count=5,
            variable_frame_rate=True,
            time_base_num=1,
            time_base_den=90_000,
        ),
    )


def _vfr_video() -> VideoFile:
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=_vfr_probe(),
        timestamp_mapping="ffprobe_pts",
    )
    center = Center.objects.create(
        name="segment-pts-center",
        display_name="Segment PTS Center",
    )
    video = VideoFile.objects.create(
        center=center,
        video_hash="segment-pts-video",
        fps=25.0,
        duration=0.3,
        frame_count=5,
        meta={"source_timeline": evidence_as_json(evidence)},
    )
    Frame.objects.bulk_create(
        [
            Frame(
                video=video,
                frame_number=frame_number,
                relative_path=f"frame_{frame_number:07d}.jpg",
                timestamp=timestamp,
            )
            for frame_number, timestamp in enumerate((0.0, 0.04, 0.11, 0.16, 0.24))
        ]
    )
    return video


def test_frame_and_timestamp_round_trip_uses_pts_not_nominal_fps() -> None:
    video = _vfr_video()

    assert video_frame_number_to_seconds(video, 2) == 0.11
    assert video_seconds_to_frame_number(video, 0.12) == 2
    assert video_seconds_to_frame_number(video, 0.12) != round(0.12 * 25.0)
    assert video_frame_number_to_seconds(video, 5) == 0.3
    assert video_seconds_to_frame_number(video, 0.3) == 5


def test_frame_neighborhood_reuses_canonical_pts_mapping() -> None:
    video = _vfr_video()

    neighborhood = get_video_frame_neighborhood(video, 0.12)

    assert neighborhood.timeline_version == "pts_v1"
    assert neighborhood.timestamp_mapping == "ffprobe_pts"
    assert neighborhood.previous is not None
    assert neighborhood.previous.to_dict() == {"frame_number": 1, "timestamp": 0.04}
    assert neighborhood.current.to_dict() == {"frame_number": 2, "timestamp": 0.11}
    assert neighborhood.next is not None
    assert neighborhood.next.to_dict() == {"frame_number": 3, "timestamp": 0.16}
    assert [frame.frame_number for frame in neighborhood.frames] == [0, 1, 2, 3, 4]


def test_frame_neighborhood_radius_limits_the_backend_owned_pts_window() -> None:
    video = _vfr_video()

    neighborhood = get_video_frame_neighborhood(video, 0.12, radius=1)

    assert [frame.to_dict() for frame in neighborhood.frames] == [
        {"frame_number": 1, "timestamp": 0.04},
        {"frame_number": 2, "timestamp": 0.11},
        {"frame_number": 3, "timestamp": 0.16},
    ]


def test_frame_neighborhood_clamps_duration_boundary_to_last_display_frame() -> None:
    video = _vfr_video()

    neighborhood = get_video_frame_neighborhood(video, 0.3)

    assert neighborhood.current.frame_number == 4
    assert neighborhood.current.timestamp == 0.24
    assert neighborhood.next is None


def test_frame_neighborhood_fails_closed_when_adjacent_vfr_pts_is_missing() -> None:
    video = _vfr_video()
    Frame.objects.filter(video=video, frame_number=3).update(timestamp=None)

    with pytest.raises(ValueError, match="has no persisted PTS"):
        get_video_frame_neighborhood(video, 0.12)


def test_vfr_frame_boundary_without_persisted_pts_fails_closed() -> None:
    video = _vfr_video()
    Frame.objects.filter(video=video, frame_number=2).update(timestamp=None)

    with pytest.raises(ValueError, match="has no persisted PTS"):
        video_frame_number_to_seconds(video, 2)


def test_segment_serializer_converts_times_with_persisted_pts() -> None:
    video = _vfr_video()
    serializer = LabelVideoSegmentSerializer(
        data={
            "video_id": video.pk,
            "start_time": 0.10,
            "end_time": 0.23,
        }
    )

    assert serializer.is_valid(), getattr(serializer, "errors")
    segment = serializer.save()

    assert segment.start_frame_number == 2
    assert segment.end_frame_number == 4
    payload = cast(
        dict[str, object],
        getattr(LabelVideoSegmentSerializer(segment), "data"),
    )
    assert payload["start_time"] == 0.11
    assert payload["end_time"] == 0.24


def test_timestamp_range_extraction_resolves_and_logs_pts_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    video = _vfr_video()
    captured: dict[str, object] = {}

    def fake_extract_video_frame_range(
        selected_video: VideoFile,
        *,
        start_frame: int,
        end_frame: int,
        overwrite: bool = False,
        **kwargs: frame_service.FrameRangeOption,
    ) -> bool:
        captured.update(
            {
                "video": selected_video,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "overwrite": overwrite,
                "kwargs": kwargs,
            }
        )
        return True

    monkeypatch.setattr(
        frame_service,
        "extract_video_frame_range",
        fake_extract_video_frame_range,
    )
    caplog.set_level("INFO", logger=frame_service.__name__)

    assert frame_service.extract_video_frame_range_by_timestamps(
        video,
        start_timestamp=0.10,
        end_timestamp=0.23,
        overwrite=True,
        quality=3,
    )

    assert captured == {
        "video": video,
        "start_frame": 2,
        "end_frame": 4,
        "overwrite": True,
        "kwargs": {"quality": 3},
    }
    assert '"event": "video_frame_range_pts_resolved"' in caplog.text
    assert '"start_frame": 2' in caplog.text
    assert '"end_frame": 4' in caplog.text


def test_segment_model_time_properties_use_pts() -> None:
    video = _vfr_video()
    segment = LabelVideoSegment.objects.create(
        video_file=video,
        start_frame_number=1,
        end_frame_number=4,
    )

    assert segment.start_time == 0.04
    assert segment.end_time == 0.24
    assert math.isclose(segment.segment_duration, 0.20)
