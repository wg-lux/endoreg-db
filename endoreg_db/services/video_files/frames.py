# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.db.models import QuerySet

if TYPE_CHECKING:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)

FrameRangeOption = bool | int | str


def extract_video_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext: str = "jpg",
    verbose: bool = False,
    from_processed: bool = False,
) -> bool:
    from ._frames import _extract_frames

    return _extract_frames(
        video,
        quality=quality,
        overwrite=overwrite,
        ext=ext,
        verbose=verbose,
        from_processed=from_processed,
    )


def initialize_video_frames(
    video: "VideoFile", frame_paths: list[Path] | None = None
) -> None:
    from ._frames import _initialize_frames

    return _initialize_frames(video, frame_paths=frame_paths)


def delete_video_frames(video: "VideoFile") -> str:
    from ._frames import _delete_frames

    return _delete_frames(video)


def extract_video_frame_range(
    video: "VideoFile",
    *,
    start_frame: int,
    end_frame: int,
    overwrite: bool = False,
    **kwargs: FrameRangeOption,
) -> bool:
    from ._frames._manage_frame_range import _extract_frame_range

    quality_raw = kwargs.get("quality", 2)
    ext_raw = kwargs.get("ext", "jpg")
    verbose_raw = kwargs.get("verbose", False)
    quality = quality_raw if isinstance(quality_raw, int) else 2
    ext = ext_raw if isinstance(ext_raw, str) else "jpg"
    verbose = verbose_raw if isinstance(verbose_raw, bool) else False
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


def extract_video_frame_range_by_timestamps(
    video: "VideoFile",
    *,
    start_timestamp: float,
    end_timestamp: float,
    overwrite: bool = False,
    **kwargs: FrameRangeOption,
) -> bool:
    """Extract a half-open frame range resolved from authoritative PTS."""
    from .metadata import video_seconds_to_frame_number

    if end_timestamp <= start_timestamp:
        raise ValueError("end_timestamp must be greater than start_timestamp")
    start_frame = video_seconds_to_frame_number(video, start_timestamp)
    end_frame = video_seconds_to_frame_number(video, end_timestamp)
    if end_frame <= start_frame:
        raise ValueError(
            "Timestamp range resolves to an empty frame interval: "
            f"[{start_frame}, {end_frame})"
        )
    logger.info(
        json.dumps(
            {
                "event": "video_frame_range_pts_resolved",
                "video_id": int(video.pk),
                "start_timestamp": float(start_timestamp),
                "end_timestamp": float(end_timestamp),
                "start_frame": start_frame,
                "end_frame": end_frame,
            },
            sort_keys=True,
        )
    )
    return extract_video_frame_range(
        video,
        start_frame=start_frame,
        end_frame=end_frame,
        overwrite=overwrite,
        **kwargs,
    )


def delete_video_frame_range(
    video: "VideoFile",
    *,
    start_frame: int,
    end_frame: int,
) -> None:
    from ._frames._manage_frame_range import _delete_frame_range

    _delete_frame_range(video=video, start_frame=start_frame, end_frame=end_frame)


def get_video_frame_path(video: "VideoFile", frame_number: int) -> Path | None:
    from ._frames import _get_frame_path

    return _get_frame_path(video, frame_number)


def get_video_frame_paths(video: "VideoFile") -> list[Path]:
    from ._frames import _get_frame_paths

    return _get_frame_paths(video)


def get_video_frame_number(video: "VideoFile") -> int:
    from ._frames import _get_frame_number

    return _get_frame_number(video)


def get_video_frames(video: "VideoFile") -> "QuerySet[Frame]":
    from ._frames import _get_frames

    return _get_frames(video)


def get_video_frame(video: "VideoFile", frame_number: int) -> "Frame":
    from ._frames import _get_frame

    return _get_frame(video, frame_number)


def get_video_frame_range(
    video: "VideoFile", start_frame_number: int, end_frame_number: int
) -> "QuerySet[Frame]":
    from ._frames import _get_frame_range

    return _get_frame_range(video, start_frame_number, end_frame_number)


def create_video_frame_object(
    video: "VideoFile",
    frame_number: int,
    relative_path: str,
    extracted: bool = False,
) -> "Frame":
    from ._frames import _create_frame_object

    return _create_frame_object(
        video,
        frame_number=frame_number,
        relative_path=relative_path,
        extracted=extracted,
    )


def bulk_create_video_frames(
    video: "VideoFile", frames_to_create: list["Frame"]
) -> None:
    from ._frames import _bulk_create_frames

    return _bulk_create_frames(video, frames_to_create)
