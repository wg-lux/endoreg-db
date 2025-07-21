import logging
from typing import TYPE_CHECKING, Optional, Dict
import cv2
from pathlib import Path

if TYPE_CHECKING:
    from ..video_file import VideoFile

def _validate_video_path(video_path: Path):
    """Validate the video file path."""
    if not isinstance(video_path, Path):
        raise TypeError("video_path must be a Path object")
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found at {video_path}")
    if not video_path.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: {video_path}")


logger = logging.getLogger(__name__)
def _get_fps(video: "VideoFile") -> float:
    """Gets the FPS, trying instance, VideoMeta, and finally direct file access."""
    from .video_meta import _update_video_meta
    if video.fps is not None:
        return video.fps

    logger.debug("FPS not set on instance %s, checking VideoMeta.", video.uuid)


    if not video.video_meta:
        logger.info("VideoMeta not linked for %s, attempting update.", video.uuid)

        _update_video_meta(video, save_instance=True) # Call the helper function

    # Check again after potential update
    if video.fps is not None:
        return video.fps
    elif video.video_meta and video.video_meta.fps is not None:
        logger.info("Retrieved FPS %.2f from VideoMeta for %s.", video.video_meta.fps, video.uuid)
        video.fps = video.video_meta.fps
        # Avoid saving if called from within the save method itself
        if not getattr(video, '_saving', False):
            video.save(update_fields=["fps"])
        return video.fps
    else:
        logger.warning("Could not determine FPS from VideoMeta for video %s. Trying direct raw file access.", video.uuid)
        try:
            if video.has_raw:
                video_path = video.get_raw_file_path() # Use helper
                if video_path and video_path.exists():
                    cap = cv2.VideoCapture(video_path.as_posix())
                    if not cap.isOpened():
                        raise IOError(f"Cannot open video file: {video_path}")
                    try:
                        fps = _get_fps_from_property(cap)

                        if fps is None or fps <= 0:
                            # Reset video capture to the beginning for manual calculation
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            fps = _calculate_fps_manually(cap, video_path)
                    finally:
                        cap.release()
                    if fps and fps > 0:
                            video.fps = fps
                            logger.info("Determined FPS %.2f directly from file for %s.", video.fps, video.uuid)
                            if not getattr(video, '_saving', False):
                                video.save(update_fields=["fps"])
                            return video.fps
                    else:
                        logger.warning("Could not determine a valid FPS for video file %s.", video_path)
                elif video_path:
                    logger.warning("Raw file path %s does not exist for direct FPS check.", video_path)
                else:
                    logger.warning("Raw file path is None for direct FPS check.")
            else:
                logger.warning("Raw file not available for direct FPS check.")

        except Exception as e:
            logger.error("Error getting FPS directly from file %s: %s", video.raw_file.name if video.has_raw else 'N/A', e)

        raise ValueError(
            f"Could not determine FPS for video {video.uuid}. "
            "Ensure the video file is valid and accessible."
        )



# TODO Refactor to utils / check if similar function exists in utils
def _get_fps_from_property(cap) -> float:
    """Get FPS from video capture property."""
    if hasattr(cv2, 'CAP_PROP_FPS'):
        return cap.get(cv2.CAP_PROP_FPS)
    # For older OpenCV versions
    return cap.get(cv2.cv.CV_CAP_PROP_FPS) # type: ignore


def _calculate_fps_manually(cap, video_path: Path) -> float:
    """Manually calculate FPS as a fallback."""
    logger.warning(f"Could not get a valid FPS for {video_path}. Trying to calculate manually.")
    # This is less accurate and slower
    num_frames = 0
    start_time = cv2.getTickCount()
    while True:
        ret, _ = cap.read()
        if not ret:
            break
        num_frames += 1
    end_time = cv2.getTickCount()
    seconds = (end_time - start_time) / cv2.getTickFrequency()
    if seconds > 0:
        return num_frames / seconds
    
    logger.error(f"Manual FPS calculation failed for {video_path} due to zero duration.")
    return 0.0