from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from rest_framework.test import APIRequestFactory

from endoreg_db.serializers.video.video_file import VideoFileSerializer


def test_video_file_serializer_video_url_returns_processed_playback_url() -> None:
    request = APIRequestFactory().get("/")
    serializer = VideoFileSerializer(context={"request": request})
    video = cast(Any, SimpleNamespace(id=7))

    assert serializer.get_video_url(video) == (
        "http://testserver/endoreg-api/media/videos/7/hls/playlist.m3u8?type=processed"
    )
