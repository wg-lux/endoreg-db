# pyright: reportUnusedFunction=false
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile


def _calc_duration_vf(obj: "VideoFile") -> float:
    """
    Calculate duration of a VideoFile.
    """
    if not obj.ffmpeg_meta:
        raise ValueError("ffmpeg_meta is missing, cannot calculate duration.")

    fps = obj.get_fps()
    frame_count = cast(
        int, obj.frame_count
    )  # TODO similar implementation as in get_fps
    if fps <= 0:
        raise ValueError(f"Invalid FPS for video {obj.video_hash}: {fps}")

    duration = frame_count / fps
    if duration > 0:
        return duration
    else:
        raise ValueError(
            f"Invalid duration calculated for video {obj.video_hash}: {duration}. "
            "Ensure the video file is valid and accessible."
        )
