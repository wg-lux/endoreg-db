from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def predict_video(video: "VideoFile", *args, **kwargs):
    from ._ai import _predict_video_pipeline

    return _predict_video_pipeline(video, *args, **kwargs)


def extract_text_from_video_frames(video: "VideoFile", *args, **kwargs):
    from ._ai import _extract_text_from_video_frames

    return _extract_text_from_video_frames(video, *args, **kwargs)
