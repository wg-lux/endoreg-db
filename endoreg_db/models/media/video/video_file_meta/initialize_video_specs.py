# --- Add Imports ---
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
import cv2
# --- End Add Imports ---

if TYPE_CHECKING:
    from ..video_file import VideoFile # Correct import path

# --- Add Logger ---
logger = logging.getLogger(__name__)
# --- End Add Logger ---

def _initialize_video_specs(video: "VideoFile", use_raw: bool = True) -> bool:
    """
    Initializes basic video specs (fps, w, h, count, duration) directly from the video file.
    Raises FileNotFoundError or RuntimeError on failure. Returns True on success or if no update needed.
    """
    video_path: Optional[Path] = None
    target_file_name: Optional[str] = None

    if use_raw and video.has_raw:
        video_path = video.get_raw_file_path() # Use IO helper
        target_file_name = video.raw_file.name
    elif video.active_file: # Fallback to active file if raw not requested or available
        video_path = video.active_file_path # Use property relying on IO helpers
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
            video_cap.release() # Ensure release
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
            video_cap.release() # Ensure release after getting props

        # --- Update FPS ---
        if current_fps is None and file_fps and file_fps > 0:
            video.fps = file_fps
            fields_to_update.append("fps")
            updated = True
            current_fps = file_fps # Update local var for duration calc

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

        # --- Update Frame Count and Duration ---
        # Only update if frame count is valid and FPS is known
        if file_frame_count > 0 and current_fps and current_fps > 0:
            if current_frame_count is None:
                video.frame_count = file_frame_count
                fields_to_update.append("frame_count")
                updated = True
            if current_duration is None:
                video.duration = file_frame_count / current_fps
                fields_to_update.append("duration")
                updated = True
        elif file_frame_count <= 0:
            logger.warning("Invalid frame count (%d) obtained from %s.", file_frame_count, video_path)
        elif not current_fps or current_fps <= 0:
            logger.warning("Cannot calculate duration for %s as FPS is unknown or invalid (%.2f).", video_path, current_fps or 0)


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
        if 'video_cap' in locals() and video_cap.isOpened():
            video_cap.release()
        # Re-raise as RuntimeError
        raise RuntimeError(f"Failed to initialize video specs for {video.uuid} from {video_path}") from e
