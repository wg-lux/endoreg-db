import pytest
import json
from unittest.mock import patch

from django.core.exceptions import FieldError
from django.db import DatabaseError, transaction
from lx_dtypes.models.meta.VideoMeta import VideoMetadataStatsPayload
from pydantic import ValidationError
from rest_framework.test import APIRequestFactory
from endoreg_db.models import (
    Center,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelVideoSegment,
    VideoFile,
)
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.video_files.segments import get_video_outside_segments
from endoreg_db.views.video.video_metadata import VideoMetadataStatsView
from tests.helpers.media_states import create_media_state


@pytest.fixture
def metadata_center() -> Center:
    return Center.objects.create(name="metadata-view-center")


@pytest.mark.django_db
def test_video_metadata_view_returns_pydantic_validated_payload(
    metadata_center: Center,
) -> None:
    factory = APIRequestFactory()
    state = create_media_state(VideoState, anonymized=True, was_created=False)
    video = VideoFile.objects.create(
        center=metadata_center,
        state=state,
        raw_video_hash="metadata-view-video-hash",
        original_file_name="metadata_view.mp4",
        duration=12.5,
        fps=25.0,
        frame_count=313,
        width=1920,
        height=1080,
    )

    request = factory.get(f"/api/media/videos/{video.pk}/metadata/")
    response = VideoMetadataStatsView.as_view()(request, pk=video.pk)

    data = json.loads(response.content.decode())

    assert response.status_code == 200
    assert response["content-type"] == "application/json"

    payload = VideoMetadataStatsPayload.model_validate(data)
    assert payload.id == video.pk
    assert payload.original_file_name == "metadata_view.mp4"
    assert payload.status == "anonymized"
    assert payload.anonymized is True
    assert payload.duration == 12.5
    assert payload.fps == 25.0
    assert payload.total_frames == 313
    assert payload.resolution == "1920x1080"
    assert payload.center_name == "metadata-view-center"
    assert payload.processor_name == "Unbekannt"
    assert payload.outside_frame_count == 0


@pytest.mark.parametrize(
    ("intervals", "expected"),
    [
        ([(2, 7)], 5),
        ([(2, 7), (10, 13)], 8),
        ([(2, 7), (5, 10)], 8),
        ([(2, 10), (4, 7)], 8),
        ([(2, 7), (7, 10)], 8),
        ([(2, 3)], 1),
    ],
)
def test_metadata_counts_unique_outside_frames(
    metadata_center: Center, intervals: list[tuple[int, int]], expected: int
) -> None:
    video = VideoFile.objects.create(
        center=metadata_center, raw_video_hash="metadata-outside-frames", frame_count=20
    )
    outside = Label.objects.create(name="OUTSIDE")
    for start, end in intervals:
        LabelVideoSegment.objects.create(
            video_file=video,
            label=outside,
            start_frame_number=start,
            end_frame_number=end,
        )

    request = APIRequestFactory().get(f"/api/media/videos/{video.pk}/metadata/")
    response = VideoMetadataStatsView.as_view()(request, pk=video.pk)

    assert response.status_code == 200
    payload = VideoMetadataStatsPayload.model_validate_json(response.content)
    assert payload.outside_frame_count == expected


def test_metadata_counts_outside_frame_annotations_without_duplicates(
    metadata_center: Center,
) -> None:
    video = VideoFile.objects.create(
        center=metadata_center, raw_video_hash="metadata-outside-annotations"
    )
    outside = Label.objects.create(name="outside")
    LabelVideoSegment.objects.create(
        video_file=video, label=outside, start_frame_number=2, end_frame_number=7
    )
    for number, value in [(4, True), (10, True), (11, False)]:
        frame = Frame.objects.create(video=video, frame_number=number)
        ImageClassificationAnnotation.objects.create(
            frame=frame, label=outside, value=value
        )

    request = APIRequestFactory().get(f"/api/media/videos/{video.pk}/metadata/")
    response = VideoMetadataStatsView.as_view()(request, pk=video.pk)

    assert response.status_code == 200
    payload = VideoMetadataStatsPayload.model_validate_json(response.content)
    assert payload.outside_frame_count == 6


@pytest.mark.parametrize("error_type", [FieldError, DatabaseError])
def test_outside_segment_selection_errors_propagate(
    metadata_center: Center,
    error_type: type[Exception],
) -> None:
    video = VideoFile.objects.create(
        center=metadata_center, raw_video_hash="outside-query-failure"
    )
    with patch.object(
        type(video.label_video_segments),
        "filter",
        side_effect=error_type("selection failed"),
    ):
        with pytest.raises(error_type, match="selection failed"):
            get_video_outside_segments(video)


@pytest.mark.parametrize("error_type", [FieldError, DatabaseError])
def test_metadata_outside_selection_errors_propagate(
    metadata_center: Center,
    error_type: type[Exception],
) -> None:
    video = VideoFile.objects.create(
        center=metadata_center, raw_video_hash="metadata-query-failure"
    )
    request = APIRequestFactory().get(f"/api/media/videos/{video.pk}/metadata/")
    with patch.object(
        LabelVideoSegment.objects, "filter", side_effect=error_type("selection failed")
    ):
        with pytest.raises(error_type, match="selection failed"):
            VideoMetadataStatsView.as_view()(request, pk=video.pk)


@pytest.mark.parametrize("metadata_request", [False, True])
def test_outside_segment_database_evaluation_errors_propagate(
    metadata_center: Center,
    metadata_request: bool,
) -> None:
    video = VideoFile.objects.create(
        center=metadata_center, raw_video_hash="outside-evaluation-failure"
    )
    invalid_query = LabelVideoSegment.objects.extra(
        where=["endoreg_missing_outside_column = 1"]
    )
    manager = (
        LabelVideoSegment.objects
        if metadata_request
        else type(video.label_video_segments)
    )
    with patch.object(manager, "filter", return_value=invalid_query):
        with pytest.raises(DatabaseError), transaction.atomic():
            if metadata_request:
                request = APIRequestFactory().get(
                    f"/api/media/videos/{video.pk}/metadata/"
                )
                VideoMetadataStatsView.as_view()(request, pk=video.pk)
            else:
                list(get_video_outside_segments(video))


def test_video_metadata_payload_rejects_invalid_ratio() -> None:
    valid_payload = {
        "id": 1,
        "original_file_name": "video.mp4",
        "status": "BLANK",
        "assigned_user": "BLANK",
        "anonymized": False,
        "duration": 0.0,
        "fps": 50.0,
        "has_roi": False,
        "outside_frame_count": 0,
        "center_name": "center",
        "processor_name": "processor",
        "sensitive_frame_count": None,
        "total_frames": None,
        "sensitive_ratio": 1.5,
        "resolution": "BLANK",
    }

    with pytest.raises(ValidationError):
        VideoMetadataStatsPayload.model_validate(valid_payload)
