from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory

from endoreg_db.models import Center, Frame, VideoFile
from endoreg_db.views import access_control
from endoreg_db.schemas.video_storage import (
    VideoArtifactProbe,
    VideoSourceTimelineEvidence,
    VideoTimelineContract,
)
from endoreg_db.services.video_storage_normalization import evidence_as_json
from endoreg_db.views.video.video_timeline import VideoFrameNeighborhoodView

pytestmark = pytest.mark.django_db


def _video_with_vfr_pts() -> VideoFile:
    timeline = VideoTimelineContract(
        fps_num=25,
        fps_den=1,
        duration_seconds=0.3,
        frame_count=5,
        variable_frame_rate=True,
        time_base_num=1,
        time_base_den=90_000,
    )
    evidence = VideoSourceTimelineEvidence(
        persisted_at=datetime.now(UTC),
        source=VideoArtifactProbe(
            codec_name="h264",
            pixel_format="yuv420p",
            width=1280,
            height=720,
            bit_rate_bps=800_000,
            size_bytes=1_000_000,
            timeline=timeline,
        ),
        timestamp_mapping="ffprobe_pts",
    )
    video = VideoFile.objects.create(
        center=Center.objects.create(name="timeline-view-center"),
        video_hash="timeline-view-video",
        fps=25.0,
        duration=0.3,
        frame_count=5,
        meta={"source_timeline": evidence_as_json(evidence)},
    )
    Frame.objects.bulk_create(
        Frame(
            video=video,
            frame_number=frame_number,
            relative_path=f"frame_{frame_number:07d}.jpg",
            timestamp=timestamp,
        )
        for frame_number, timestamp in enumerate((0.0, 0.04, 0.11, 0.16, 0.24))
    )
    return video


def test_frame_neighborhood_view_returns_canonical_pts() -> None:
    video = _video_with_vfr_pts()
    request = APIRequestFactory().get(
        f"/api/media/videos/{video.pk}/timeline/frame-neighborhood/",
        {"timestamp": "0.12"},
    )

    response = VideoFrameNeighborhoodView.as_view()(request, pk=video.pk)

    assert response.status_code == 200
    assert response.data == {
        "video_id": video.pk,
        "requested_timestamp": 0.12,
        "timeline_version": "pts_v1",
        "timestamp_mapping": "ffprobe_pts",
        "previous": {"frame_number": 1, "timestamp": 0.04},
        "current": {"frame_number": 2, "timestamp": 0.11},
        "next": {"frame_number": 3, "timestamp": 0.16},
        "frames": [
            {"frame_number": 0, "timestamp": 0.0},
            {"frame_number": 1, "timestamp": 0.04},
            {"frame_number": 2, "timestamp": 0.11},
            {"frame_number": 3, "timestamp": 0.16},
            {"frame_number": 4, "timestamp": 0.24},
        ],
    }


def test_frame_neighborhood_view_has_a_constant_four_query_budget() -> None:
    video = _video_with_vfr_pts()
    request = APIRequestFactory().get(
        f"/api/media/videos/{video.pk}/timeline/frame-neighborhood/",
        {"timestamp": "0.12", "radius": "1"},
    )

    with CaptureQueriesContext(connection) as queries:
        response = VideoFrameNeighborhoodView.as_view()(request, pk=video.pk)

    assert response.status_code == 200
    assert len(queries) == 4


def test_frame_model_has_timestamp_lookup_index() -> None:
    assert any(
        index.name == "frame_video_timestamp_idx"
        and index.fields == ["video", "timestamp"]
        for index in Frame._meta.indexes
    )


def test_frame_neighborhood_view_rejects_invalid_timestamp() -> None:
    video = _video_with_vfr_pts()
    request = APIRequestFactory().get(
        f"/api/media/videos/{video.pk}/timeline/frame-neighborhood/",
        {"timestamp": "not-a-number"},
    )

    response = VideoFrameNeighborhoodView.as_view()(request, pk=video.pk)

    assert response.status_code == 400


def test_frame_neighborhood_view_fails_closed_for_missing_vfr_pts() -> None:
    video = _video_with_vfr_pts()
    Frame.objects.filter(video=video, frame_number=3).update(timestamp=None)
    request = APIRequestFactory().get(
        f"/api/media/videos/{video.pk}/timeline/frame-neighborhood/",
        {"timestamp": "0.12"},
    )

    response = VideoFrameNeighborhoodView.as_view()(request, pk=video.pk)

    assert response.status_code == 422
    assert response.data["error"] == "Frame neighborhood is unavailable."


def test_frame_neighborhood_view_is_center_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video_with_vfr_pts()

    def resolve_other_center(_user: object) -> int:
        return int(video.center_id) + 1

    monkeypatch.setattr(
        access_control,
        "resolve_allowed_center_id",
        resolve_other_center,
    )
    request = APIRequestFactory().get(
        f"/api/media/videos/{video.pk}/timeline/frame-neighborhood/",
        {"timestamp": "0.12"},
    )

    response = VideoFrameNeighborhoodView.as_view()(request, pk=video.pk)

    assert response.status_code == 404
