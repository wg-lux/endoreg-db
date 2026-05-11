from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

import cv2

from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.video import ffmpeg_wrapper

if TYPE_CHECKING:
    from ..video_file import VideoFile


def _validate_video_path(video_path: Path):
    """
    Validates that the provided path is an existing video file.

    Raises:
        TypeError: If `video_path` is not a Path object.
        FileNotFoundError: If the file does not exist at the specified path.
        IsADirectoryError: If the path points to a directory instead of a file.
    """
    if not isinstance(video_path, Path):
        raise TypeError("video_path must be a Path object")
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found at {video_path}")
    if not video_path.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: {video_path}")


logger = logging.getLogger(__name__)


def _is_valid_fps(value: Any) -> bool:
    try:
        fps = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(fps) and fps > 0


def _parse_frame_rate(frame_rate: Any) -> Optional[float]:
    if frame_rate is None:
        return None

    if isinstance(frame_rate, (int, float)):
        fps = float(frame_rate)
        return fps if _is_valid_fps(fps) else None

    if isinstance(frame_rate, str):
        value = frame_rate.strip()
        if not value:
            return None
        if "/" in value:
            num_str, den_str = value.split("/", 1)
            try:
                numerator = float(num_str)
                denominator = float(den_str)
            except ValueError:
                return None
            if denominator == 0:
                return None
            fps = numerator / denominator
            return fps if _is_valid_fps(fps) else None
        try:
            fps = float(value)
        except ValueError:
            return None
        return fps if _is_valid_fps(fps) else None

    return None


def _get_fps_from_ffprobe(video_path: Path) -> Optional[float]:
    stream_info = ffmpeg_wrapper.get_stream_info(video_path)
    if not stream_info or "streams" not in stream_info:
        return None

    video_stream = next(
        (s for s in stream_info["streams"] if s.get("codec_type") == "video"),
        None,
    )
    if not isinstance(video_stream, dict):
        return None

    avg_rate = _parse_frame_rate(video_stream.get("avg_frame_rate"))
    if avg_rate is not None:
        return avg_rate

    raw_rate = _parse_frame_rate(video_stream.get("r_frame_rate"))
    if raw_rate is not None:
        return raw_rate

    return None


def _resolve_active_field_file(video: "VideoFile"):
    try:
        return video.active_file
    except Exception:
        return None


def _get_fps_from_video_file(video: "VideoFile") -> Optional[float]:
    active_file = _resolve_active_field_file(video)
    if active_file is None:
        return None

    try:
        with ensure_local_file(active_file) as video_path:
            _validate_video_path(video_path)

            ffprobe_fps = _get_fps_from_ffprobe(video_path)
            if ffprobe_fps is not None:
                return ffprobe_fps

            cap = cast(Any, cv2.VideoCapture)(video_path.as_posix())
            if not cap.isOpened():
                logger.warning("Cannot open video file for FPS read: %s", video_path)
                cap.release()
                return None
            try:
                fps = _get_fps_from_property(cap)
            finally:
                cap.release()

            if _is_valid_fps(fps):
                return float(fps)
    except Exception as exc:
        logger.warning(
            "Could not stage active video file locally while resolving FPS for %s: %s",
            getattr(video, "video_hash", "<unknown>"),
            exc,
        )

    return None


def _persist_video_fps(video: "VideoFile", fps: float) -> float:
    fps_value = float(fps)

    if _is_valid_fps(video.fps) and math.isclose(
        float(cast(Any, video.fps)),
        fps_value,
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        setattr(video, "_fps_verified", True)
        return fps_value

    video.fps = fps_value
    setattr(video, "_fps_verified", True)
    if getattr(video, "pk", None) and not getattr(video, "_saving", False):
        video.save(update_fields=["fps"])
    return fps_value


def _get_fps(video: "VideoFile") -> float:
    """
    Determine and return the frames per second (FPS) of a video associated with a VideoFile instance.

    Attempts to retrieve FPS from the instance itself, its linked VideoMeta, or by direct analysis of the raw video file using OpenCV. Updates and saves the FPS value to the instance if successfully determined. Raises a ValueError if FPS cannot be determined by any method.

    Returns:
        float: The frames per second (FPS) of the video.

    Raises:
        ValueError: If the FPS cannot be determined from any available source.
    """
    from .video_meta import _update_video_meta

    if getattr(video, "_fps_verified", False) and _is_valid_fps(video.fps):
        return float(cast(Any, video.fps))

    file_fps = _get_fps_from_video_file(video)
    if file_fps is not None:
        logger.debug(
            "Resolved FPS %.6f from active video file for %s.",
            file_fps,
            video.video_hash,
        )
        return _persist_video_fps(video, file_fps)

    if _is_valid_fps(video.fps):
        logger.warning(
            "Using cached FPS %.6f for %s because active file was unavailable.",
            float(cast(Any, video.fps)),
            video.video_hash,
        )
        return float(cast(Any, video.fps))

    logger.debug("FPS not available on %s, checking VideoMeta.", video.video_hash)

    if not video.video_meta:
        logger.info("VideoMeta not linked for %s, attempting update.", video.video_hash)
        try:
            _update_video_meta(video, save_instance=True)
        except Exception as exc:
            logger.warning(
                "VideoMeta update failed for %s while resolving FPS: %s",
                video.video_hash,
                exc,
            )

    meta_fps = getattr(getattr(video, "video_meta", None), "fps", None)
    if _is_valid_fps(meta_fps):
        logger.info(
            "Retrieved FPS %.6f from VideoMeta for %s.",
            float(cast(Any, meta_fps)),
            video.video_hash,
        )
        return _persist_video_fps(video, float(cast(Any, meta_fps)))

    if getattr(video, "use_default_fps", False):
        default_fps = float(video.ensure_default_fps())
        if _is_valid_fps(default_fps):
            logger.warning(
                "Falling back to default FPS %.6f for %s because no verifiable file FPS was found.",
                default_fps,
                video.video_hash,
            )
            return default_fps

    raise ValueError(
        f"Could not determine FPS from the actual video file for {video.video_hash}. "
        "Ensure the file exists and has valid stream metadata."
    )


# TODO Refactor to utils / check if similar function exists in utils
def _get_fps_from_property(cap: cv2.VideoCapture) -> float:
    """
    Retrieve the frames per second (FPS) from an OpenCV video capture object using the appropriate property for the OpenCV version.

    Parameters:
        cap: An OpenCV video capture object.

    Returns:
        float: The FPS value obtained from the video capture properties, or 0.0 if unavailable.
    """
    return cap.get(cv2.CAP_PROP_FPS)
