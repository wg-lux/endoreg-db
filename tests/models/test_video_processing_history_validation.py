from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory


def _video() -> VideoFile:
    center = Center.objects.create(
        name="video-processing-history-validation-center",
    )
    return VideoFile.objects.create(
        video_hash="video-processing-history-validation",
        center=center,
    )


@pytest.mark.django_db
def test_video_processing_history_canonicalizes_config_on_direct_save() -> None:
    history = VideoProcessingHistory.objects.create(
        video=_video(),
        operation=VideoProcessingHistory.OPERATION_FRAME_REMOVAL,
        config={"frames_to_remove": [3, 7], "nested": {"enabled": True}},
    )

    assert history.config == {
        "frames_to_remove": [3, 7],
        "nested": {"enabled": True},
    }
    history.refresh_from_db()
    assert history.config["frames_to_remove"] == [3, 7]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "config",
    [
        [],
        {1: "not-a-json-key"},
        {"frame_count": object()},
        {"threshold": float("nan")},
    ],
)
def test_video_processing_history_rejects_invalid_config_at_model_boundary(
    config: object,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        VideoProcessingHistory.objects.create(
            video=_video(),
            operation=VideoProcessingHistory.OPERATION_ANALYSIS,
            config=config,
        )

    assert "config" in exc_info.value.message_dict
