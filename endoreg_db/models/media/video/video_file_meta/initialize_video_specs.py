# --- Add Imports ---
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import cv2

# --- End Add Imports ---

if TYPE_CHECKING:
    from ..video_file import VideoFile  # Correct import path

# --- Add Logger ---
logger = logging.getLogger(__name__)
# --- End Add Logger ---


def _initialize_video_specs(video: "VideoFile", use_raw: bool = True) -> bool:
    """
    Initializes video specifications for a VideoFile object by reading from the video file.

    Attempts to populate missing values for fps, width, height, frame count, and duration using OpenCV. Selects the raw file if available and requested, otherwise uses the active file. Updates only unset fields if valid values are obtained. Returns True if successful or if no updates are needed. Raises FileNotFoundError if the video file cannot be found, or RuntimeError if the file cannot be opened or properties cannot be read.
    """
    video_path: Optional[Path] = None
    target_file_name: Optional[str] = None

    if use_raw and video.has_raw:
        video_path = video.get_raw_file_path()  # Use IO helper
        target_file_name = video.raw_file.name
    elif video.active_file:  # Fallback to active file if raw not requested or available
        video_path = video.active_file_path  # Use property relying on IO helpers
        target_file_name = video.active_file.name
    else:
        logger.error("No suitable video file found for spec initialization for %s.", video.uuid)
        return False

    if not video_path:
        # Raise exception
        raise FileNotFoundError(f"Could not determine video file path for spec initialization for {video.uuid}.")

    logger.info("Initializing video specs directly from file %s (%s) for %s", target_file_name, video_path, video.uuid)
    try:
        if not video_path.exists():
            # Raise exception
            raise FileNotFoundError(f"Video file not found at {video_path} for spec initialization (Video: {video.uuid}).")

        video_cap = cv2.VideoCapture(video_path.as_posix())
        if not video_cap.isOpened():
            # Raise exception
            video_cap.release()  # Ensure release
            raise RuntimeError(f"Could not open video file {video_path} with OpenCV for spec initialization (Video: {video.uuid}).")

        updated = False
        fields_to_update = []

        # Get current values before checking
        current_fps = video.fps
        current_width = video.width
        current_height = video.height
        current_frame_count = video.frame_count
        current_duration = video.duration

        # --- Get values from OpenCV ---
        try:
            file_fps = video_cap.get(cv2.CAP_PROP_FPS)
            file_width = int(video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            file_height = int(video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            file_frame_count = int(video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        except Exception as cv_err:
            logger.error("Error getting properties from OpenCV for %s (Video: %s): %s", video_path, video.uuid, cv_err, exc_info=True)
            raise RuntimeError(f"OpenCV failed to get properties for {video_path}") from cv_err
        finally:
            video_cap.release()  # Ensure release after getting props

        # --- Update FPS ---
        if current_fps is None and file_fps and file_fps > 0:
            video.fps = file_fps
            fields_to_update.append("fps")
            updated = True
            current_fps = file_fps  # Update local var for duration calc

        # --- Update Width ---
        if current_width is None and file_width > 0:
            video.width = file_width
            fields_to_update.append("width")
            updated = True

        # --- Update Height ---
        if current_height is None and file_height > 0:
            video.height = file_height
            fields_to_update.append("height")
            updated = True

        # --- Update Frame Count ---
        if current_frame_count is None and file_frame_count and file_frame_count > 0:
            video.frame_count = file_frame_count
            fields_to_update.append("frame_count")
            updated = True
        elif file_frame_count is None or file_frame_count <= 0:  # Log if not updated due to invalid file_frame_count
            logger.warning("Invalid frame count (value: %s) obtained from OpenCV for %s. Video frame_count not updated.", file_frame_count, video_path)

        # --- Update Duration ---
        if current_duration is None:  # Only if duration isn't already set
            # Use the potentially updated video.frame_count and current_fps (which reflects video.fps or file_fps)
            final_frame_count_for_duration = video.frame_count
            final_fps_for_duration = current_fps  # This is video.fps after potential update from file_fps

            if final_frame_count_for_duration and final_frame_count_for_duration > 0 and final_fps_for_duration and final_fps_for_duration > 0:
                video.duration = final_frame_count_for_duration / final_fps_for_duration
                fields_to_update.append("duration")
                updated = True
            else:
                # Log if duration could not be calculated, indicating which component was missing/invalid
                if not (final_frame_count_for_duration and final_frame_count_for_duration > 0):
                    logger.warning(
                        "Duration not calculated for %s: frame count is unavailable or invalid (value: %s).", video_path, final_frame_count_for_duration
                    )
                if not (final_fps_for_duration and final_fps_for_duration > 0):
                    logger.warning("Duration not calculated for %s: FPS is unavailable or invalid (value: %s).", video_path, final_fps_for_duration)

        # --- Save if updated ---
        if updated:
            logger.info("Updated video specs for %s from file %s: %s", video.uuid, target_file_name, ", ".join(fields_to_update))
            video.save(update_fields=fields_to_update)
            return True
        else:
            logger.info("No video specs needed updating for %s from file %s.", video.uuid, target_file_name)
            return True

    except Exception as e:
        # Log and re-raise exception
        logger.error("Error initializing video specs for %s from file %s: %s", video.uuid, video_path, e, exc_info=True)
        # Ensure capture is released in case of unexpected error
        if "video_cap" in locals() and video_cap.isOpened():
            video_cap.release()
        # Re-raise as RuntimeError
        raise RuntimeError(f"Failed to initialize video specs for {video.uuid} from {video_path}") from e
