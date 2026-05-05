from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .video_file import VideoFile


def _ensure_default_fps(video: "VideoFile") -> float:
    """
    Persist the configured default FPS when the video has no FPS value.
    """
    if video.fps is not None:
        return float(video.fps)

    default_fps = float(video.default_fps)
    video.fps = default_fps
    if video.pk:
        video.save(update_fields=["fps"])
    return default_fps


def _frame_number_to_s(video: "VideoFile", frame_number: int) -> float:
    """
    Convert a frame number to its corresponding time in seconds.
    """
    fps = video.fps
    if fps is None or fps <= 0:
        fps = video.get_fps()
    if fps is None or fps <= 0:
        raise ValueError("FPS must be set and greater than zero.")
    return frame_number / fps
