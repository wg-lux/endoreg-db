import logging
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

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

    if video.fps is not None:
        return video.fps

    logger.debug("FPS not set on instance %s, checking VideoMeta.", video.uuid)

    if not video.video_meta:
        logger.info("VideoMeta not linked for %s, attempting update.", video.uuid)

        _update_video_meta(video, save_instance=True)  # Call the helper function

    # Check again after potential update
    if video.fps is not None:
        return video.fps
    elif video.video_meta and video.video_meta.fps is not None:
        logger.info("Retrieved FPS %.2f from VideoMeta for %s.", video.video_meta.fps, video.uuid)
        _fps = video.video_meta.fps
        try:
            _fps = float(_fps)
        except (TypeError, ValueError):
            logger.warning("Invalid FPS value %.2f in VideoMeta for video %s.", video.video_meta.fps, video.uuid)
            raise ValueError(f"Could not determine FPS for video {video.uuid} due to invalid VideoMeta FPS value.")
        video.fps = _fps
        # Avoid saving if called from within the save method itself
        if not getattr(video, "_saving", False):
            video.save(update_fields=["fps"])
        return _fps
    else:
        logger.warning("Could not determine FPS from VideoMeta for video %s. Trying direct raw file access.", video.uuid)
        try:
            if video.has_raw:
                video_path = video.get_raw_file_path()  # Use helper
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
                        if not getattr(video, "_saving", False):
                            video.save(update_fields=["fps"])
                        return fps
                    else:
                        logger.warning("Could not determine a valid FPS for video file %s.", video_path)
                elif video_path:
                    logger.warning("Raw file path %s does not exist for direct FPS check.", video_path)
                else:
                    logger.warning("Raw file path is None for direct FPS check.")
            else:
                logger.warning("Raw file not available for direct FPS check.")

        except Exception as e:
            logger.error("Error getting FPS directly from file %s: %s", video.raw_file.name if video.has_raw else "N/A", e)

        raise ValueError(f"Could not determine FPS for video {video.uuid}. Ensure the video file is valid and accessible.")


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


def _calculate_fps_manually(cap, video_path: Path) -> float:
    """
    Estimate the frames per second (FPS) of a video by reading all frames and dividing the total frame count by the elapsed time.

    Parameters:
        cap: An OpenCV video capture object positioned at the start of the video.
        video_path (Path): Path to the video file, used for logging.

    Returns:
        float: The estimated FPS, or 0.0 if the duration is zero or calculation fails.
    """
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
