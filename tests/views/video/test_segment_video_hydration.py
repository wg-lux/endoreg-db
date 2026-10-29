"""Segment list, validation and bulk paths load each video once, not per row."""

# pyright: reportPrivateUsage=false
import json
from typing import Any, cast
from unittest.mock import patch

import pytest
from django.http import Http404
from rest_framework.test import APIRequestFactory

from endoreg_db.models import (
    Center,
    Frame,
    InformationSource,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.services.annotations import segments_bulk_mutation
from endoreg_db.views.video import segments_crud
from endoreg_db.views.video.segments_crud import (
    video_segments_by_video,
    video_segments_collection,
)

FRAMES_PER_SEGMENT = 3


def _video_with_segments(segment_count: int) -> tuple[VideoFile, list[int]]:
    video = VideoFile.objects.create(
        center=Center.objects.create(name="hydration-center"),
        raw_video_hash="hydration-video",
        original_file_name="hydration.mp4",
        fps=25.0,
        frame_count=1000,
    )
    label = Label.objects.create(name="hydration-label")
    source = InformationSource.objects.create(name="hydration-source")
    Frame.objects.bulk_create(
        Frame(
            video=video,
            frame_number=number,
            relative_path=f"frame_{number:07d}.jpg",
        )
        for number in range(segment_count * FRAMES_PER_SEGMENT)
    )
    segment_ids = [
        int(
            LabelVideoSegment.objects.create(
                video_file=video,
                label=label,
                source=source,
                start_frame_number=index * FRAMES_PER_SEGMENT,
                end_frame_number=(index + 1) * FRAMES_PER_SEGMENT,
            ).pk
        )
        for index in range(segment_count)
    ]
    return video, segment_ids


@pytest.mark.django_db
@pytest.mark.parametrize("segment_count", [1, 6])
def test_video_segment_list_loads_video_once_with_annotation_payload(
    segment_count: int,
) -> None:
    video, _ = _video_with_segments(segment_count)
    request = APIRequestFactory().get(
        f"/api/media/videos/{video.pk}/segments/?include_annotation_payload=1"
    )

    with patch.object(VideoFile, "from_db", wraps=VideoFile.from_db) as loaded:
        response = video_segments_by_video(request, pk=int(video.pk))
        rendered = cast(Any, response).render()

    assert response.status_code == 200
    # One load for the route lookup and one shared prefetch, independent of
    # segment and frame count.
    assert loaded.call_count == 2
    data = json.loads(rendered.content.decode())
    assert len(data) == segment_count
    assert all(
        len(row["time_segments"]["frames"]) == FRAMES_PER_SEGMENT for row in data
    )


@pytest.mark.django_db
@pytest.mark.parametrize("segment_count", [1, 6])
def test_segment_collection_list_loads_video_once(segment_count: int) -> None:
    video, _ = _video_with_segments(segment_count)
    request = APIRequestFactory().get(
        f"/api/media/segments/?video_id={video.pk}&include_annotation_payload=1"
    )

    with patch.object(VideoFile, "from_db", wraps=VideoFile.from_db) as loaded:
        response = video_segments_collection(request)
        rendered = cast(Any, response).render()

    assert response.status_code == 200
    assert loaded.call_count == 2
    assert len(json.loads(rendered.content.decode())) == segment_count


@pytest.mark.django_db
@pytest.mark.parametrize("segment_count", [1, 6])
def test_bulk_validation_segments_share_one_video(segment_count: int) -> None:
    video, segment_ids = _video_with_segments(segment_count)

    with patch.object(VideoFile, "from_db", wraps=VideoFile.from_db) as loaded:
        segments = segments_crud._bulk_validation_segments(
            video=video, segment_ids=segment_ids
        )
        assert loaded.call_count == 1

    assert len(segments) == segment_count
    first_video = cast(Any, segments[0]).video_file
    assert all(cast(Any, segment).video_file is first_video for segment in segments)


@pytest.mark.django_db
def test_bulk_mutation_segment_reuses_request_video() -> None:
    video, segment_ids = _video_with_segments(2)

    with patch.object(VideoFile, "from_db", wraps=VideoFile.from_db) as loaded:
        segments = [
            segments_bulk_mutation._bulk_segment_for_video(video, segment_id)
            for segment_id in segment_ids
        ]
        assert loaded.call_count == 0

    assert all(segment.video_file is video for segment in segments)


@pytest.mark.django_db
def test_bulk_mutation_segment_rejects_segment_of_other_video() -> None:
    video, segment_ids = _video_with_segments(1)
    other_video = VideoFile.objects.create(
        center=video.center, raw_video_hash="hydration-other-video"
    )

    with pytest.raises(Http404):
        segments_bulk_mutation._bulk_segment_for_video(other_video, segment_ids[0])
