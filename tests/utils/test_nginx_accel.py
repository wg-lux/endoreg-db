from __future__ import annotations

import pytest

from endoreg_db.utils.web.nginx_accel import build_nginx_accel_response


def test_nginx_accel_rejects_unsafe_relative_path():
    with pytest.raises(ValueError):
        build_nginx_accel_response(
            protected_relative_path="../escape.mp4",
            content_type="video/mp4",
        )


def test_nginx_accel_builds_internal_redirect_for_safe_path(monkeypatch):
    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")

    response = build_nginx_accel_response(
        protected_relative_path="streamable_videos/processed/test.mp4",
        content_type="video/mp4",
    )

    assert (
        response["X-Accel-Redirect"]
        == "/protected_media/streamable_videos/processed/test.mp4"
    )
