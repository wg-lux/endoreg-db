from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.test import Client

from endoreg_db.views.video import video_stream as view_module


def _legacy_video(pk: int) -> SimpleNamespace:
    return SimpleNamespace(pk=pk)


def _patched_get_video_or_404(pk: int | str | None) -> SimpleNamespace:
    if isinstance(pk, int):
        return _legacy_video(pk)
    if isinstance(pk, str):
        return _legacy_video(int(pk))
    return _legacy_video(123)


@pytest.mark.django_db
def test_legacy_video_stream_redirects_to_processed_hls(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        view_module.VideoStreamView,
        "_get_video_or_404",
        cast(Any, staticmethod(_patched_get_video_or_404)),
    )

    response = client.get("/api/media/videos/123/stream/?type=raw")

    assert response.status_code == 302
    assert response["X-Stream-State"] == view_module.LEGACY_VIDEO_STREAM_STATE
    assert "X-Accel-Redirect" not in response.headers
    assert (
        response["Location"]
        == "http://testserver/endoreg-api/media/videos/123/hls/playlist.m3u8?type=processed"
    )
    assert (
        response["Link"]
        == "<http://testserver/endoreg-api/media/videos/123/hls/playlist.m3u8?type=processed>; "
        'rel="alternate"; type="application/vnd.apple.mpegurl"'
    )


@pytest.mark.django_db
def test_legacy_video_detail_stream_alias_redirects_to_processed_hls(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        view_module.VideoStreamView,
        "_get_video_or_404",
        cast(Any, staticmethod(_patched_get_video_or_404)),
    )

    response = client.get("/api/media/videos/123/")

    assert response.status_code == 302
    assert response["X-Stream-State"] == view_module.LEGACY_VIDEO_STREAM_STATE
    assert (
        response["Location"]
        == "http://testserver/endoreg-api/media/videos/123/hls/playlist.m3u8?type=processed"
    )


@pytest.mark.django_db
def test_legacy_video_stream_preserves_configured_cors(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DJANGO_CORS_ALLOWED_ORIGINS", "http://frontend.test")
    monkeypatch.setattr(
        view_module.VideoStreamView,
        "_get_video_or_404",
        cast(Any, staticmethod(_patched_get_video_or_404)),
    )

    response = client.get(
        "/api/media/videos/123/stream/",
        HTTP_ORIGIN="http://frontend.test",
    )

    assert response.status_code == 302
    assert response["Access-Control-Allow-Origin"] == "http://frontend.test"
