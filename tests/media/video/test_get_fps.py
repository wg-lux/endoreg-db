# pyright: reportPrivateUsage=false
import importlib
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import cv2
import pytest
from pytest import MonkeyPatch

from endoreg_db.services.video_files._metadata import get_fps as get_fps_module
from endoreg_db.services.video_files._metadata.get_fps import _get_fps
from endoreg_db.models.media.video.video_file import VideoFile


def _build_video(
    *,
    fps: float | None = False,
    use_default_fps: bool = False,
):
    return SimpleNamespace(
        fps=fps,
        use_default_fps=use_default_fps,
        video_meta=None,
        video_hash="video-hash-test",
        pk=1,
        _saving=False,
        save=MagicMock(),
        ensure_default_fps=MagicMock(return_value=50.0),
    )


def test_get_fps_module_imports_when_cv2_video_capture_is_unavailable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delattr(cv2, "VideoCapture", raising=False)

    importlib.reload(get_fps_module)


def test_get_fps_prefers_file_based_value_over_cached_field():
    video = _build_video(fps=50.0, use_default_fps=False)

    with patch(
        "endoreg_db.services.video_files._metadata.get_fps._get_fps_from_video_file",
        return_value=29.97,
    ):
        resolved_fps = _get_fps(cast(VideoFile, video))

    assert resolved_fps == 29.97
    assert video.fps == 29.97
    video.save.assert_called_once_with(update_fields=["fps"])
    assert getattr(video, "_fps_verified", False) is True


def test_get_fps_uses_cached_value_when_no_file_source():
    video = _build_video(fps=25.0, use_default_fps=False)

    with patch(
        "endoreg_db.services.video_files._metadata.get_fps._get_fps_from_video_file",
        return_value=None,
    ):
        resolved_fps = _get_fps(cast(VideoFile, video))

    assert resolved_fps == 25.0
    video.save.assert_not_called()


def test_get_fps_uses_default_only_when_explicitly_enabled():
    video = _build_video(fps=None, use_default_fps=True)

    with (
        patch(
            "endoreg_db.services.video_files._metadata.get_fps._get_fps_from_video_file",
            return_value=None,
        ),
        patch(
            "endoreg_db.services.video_files._metadata.video_meta._update_video_meta",
            return_value=None,
        ),
    ):
        resolved_fps = _get_fps(cast(VideoFile, video))

    assert resolved_fps == 50.0
    video.ensure_default_fps.assert_called_once()


def test_get_fps_errs_when_no_file_and_no_fps_fallback():
    video = _build_video(fps=None, use_default_fps=False)

    with (
        patch(
            "endoreg_db.services.video_files._metadata.get_fps._get_fps_from_video_file",
            return_value=None,
        ),
        patch(
            "endoreg_db.services.video_files._metadata.video_meta._update_video_meta",
            return_value=None,
        ),
    ):
        with pytest.raises(ValueError, match="Could not determine FPS"):
            _get_fps(cast(VideoFile, video))
