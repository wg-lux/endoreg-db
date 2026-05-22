from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def extract_video_frames(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _extract_frames

    return _extract_frames(video, *args, **kwargs)


def initialize_video_frames(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _initialize_frames

    return _initialize_frames(video, *args, **kwargs)


def delete_video_frames(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _delete_frames

    return _delete_frames(video, *args, **kwargs)


def extract_video_frame_range(
    video: "VideoFile",
    *,
    start_frame: int,
    end_frame: int,
    overwrite: bool = False,
    **kwargs,
) -> bool:
    from endoreg_db.models.media.video.video_file_frames._manage_frame_range import (
        _extract_frame_range,
    )

    quality = kwargs.get("quality", 2)
    ext = kwargs.get("ext", "jpg")
    verbose = kwargs.get("verbose", False)
    expected_kwargs = {"quality", "ext", "verbose"}
    unexpected_kwargs = {
        key: value for key, value in kwargs.items() if key not in expected_kwargs
    }
    if unexpected_kwargs:
        logger.warning(
            "Unexpected keyword arguments for extract_video_frame_range ignored by helper: %s",
            unexpected_kwargs,
        )

    return _extract_frame_range(
        video=video,
        start_frame=start_frame,
        end_frame=end_frame,
        quality=quality,
        overwrite=overwrite,
        ext=ext,
        verbose=verbose,
    )


def delete_video_frame_range(
    video: "VideoFile",
    *,
    start_frame: int,
    end_frame: int,
) -> None:
    from endoreg_db.models.media.video.video_file_frames._manage_frame_range import (
        _delete_frame_range,
    )

    _delete_frame_range(video=video, start_frame=start_frame, end_frame=end_frame)


def get_video_frame_path(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _get_frame_path

    return _get_frame_path(video, *args, **kwargs)


def get_video_frame_paths(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _get_frame_paths

    return _get_frame_paths(video, *args, **kwargs)


def get_video_frame_number(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _get_frame_number

    return _get_frame_number(video, *args, **kwargs)


def get_video_frames(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _get_frames

    return _get_frames(video, *args, **kwargs)


def get_video_frame(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _get_frame

    return _get_frame(video, *args, **kwargs)


def get_video_frame_range(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _get_frame_range

    return _get_frame_range(video, *args, **kwargs)


def create_video_frame_object(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _create_frame_object

    return _create_frame_object(video, *args, **kwargs)


def bulk_create_video_frames(video: "VideoFile", *args, **kwargs):
    from endoreg_db.models.media.video.video_file_frames import _bulk_create_frames

    return _bulk_create_frames(video, *args, **kwargs)
