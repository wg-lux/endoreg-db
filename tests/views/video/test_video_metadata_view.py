import pytest
import json
from lx_dtypes.models.meta.VideoMeta import VideoMetadataStatsPayload
from pydantic import ValidationError
from rest_framework.test import APIRequestFactory
from endoreg_db.models import Center, VideoFile
from endoreg_db.models.state.video import VideoState
from endoreg_db.views.video.video_metadata import VideoMetadataStatsView


@pytest.mark.django_db
def test_video_metadata_view_returns_pydantic_validated_payload() -> None:
    factory = APIRequestFactory()
    center = Center.objects.create(name="metadata-view-center")
    state = VideoState.objects.create(anonymized=True, was_created=False)
    video = VideoFile.objects.create(
        center=center,
        state=state,
        video_hash="metadata-view-video-hash",
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
