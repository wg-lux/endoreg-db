from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from rest_framework.test import APIRequestFactory

from endoreg_db.serializers.video.video_file import VideoFileSerializer

pytestmark = pytest.mark.django_db


def test_video_file_serializer_video_url_returns_processed_playback_url() -> None:
    request = APIRequestFactory().get("/")
    serializer = VideoFileSerializer(context={"request": request})
    video = cast(Any, SimpleNamespace(id=7))

    assert serializer.get_video_url(video) == (
        "http://testserver/endoreg-api/media/videos/7/hls/playlist.m3u8?type=processed"
    )


@pytest.mark.parametrize("duration", [None, 0.0, 125.5])
def test_duration_read_never_opens_or_materializes_media(
    duration: float | None,
) -> None:
    video = cast(Any, SimpleNamespace(duration=duration))
    with patch(
        "endoreg_db.serializers.video.video_file.get_active_video_file",
        side_effect=AssertionError("metadata reads must not access media"),
    ):
        assert VideoFileSerializer().get_duration(video) == duration
