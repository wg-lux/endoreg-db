import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from icecream import ic

from ...utils import data_paths, ANONYM_VIDEO_DIR # Import necessary paths

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger("video_file")

def _get_raw_file_path(video: "VideoFile") -> Optional[Path]:
    """Returns the absolute Path object for the raw file, if it exists."""
    try:
        if video.has_raw and video.raw_file.name:
            return Path(video.raw_file.path)
        else:
            return None
    except Exception as e:
        logger.warning("Could not get path for raw file of VideoFile %s: %s", video.uuid, e)
        return None

def _get_processed_file_path(video: "VideoFile") -> Optional[Path]:
    """Returns the absolute Path object for the processed file, if it exists."""
    try:
        if video.is_processed and video.processed_file.name:
            return Path(video.processed_file.path)
        else:
            return None
    except Exception as e:
        logger.warning("Could not get path for processed file of VideoFile %s: %s", video.uuid, e)
        return None

def _delete_with_file(video: "VideoFile", *args, **kwargs):
    """Deletes the VideoFile record and its associated physical files (raw, processed, frames)."""
    # 1. Delete Frames (using the frame helper function via instance method)
    frame_delete_msg = video.delete_frames()
    logger.info(frame_delete_msg)

    # 2. Delete Raw File
    raw_file_path = _get_raw_file_path(video)
    if raw_file_path:
        try:
            if raw_file_path.exists():
                raw_file_path.unlink()
                logger.info("Deleted raw video file: %s", raw_file_path)
            else:
                logger.warning("Raw video file not found at %s, skipping deletion.", raw_file_path)
                
        except Exception as e:
            logger.error("Error deleting raw video file %s: %s", raw_file_path, e, exc_info=True)
            
    # 3. Delete Processed File
    processed_file_path = _get_processed_file_path(video)
    if processed_file_path:
        try:
            if processed_file_path.exists():
                processed_file_path.unlink()
                logger.info("Deleted processed video file: %s", processed_file_path)
            else:
                logger.warning("Processed video file not found at %s, skipping deletion.", processed_file_path)
        except Exception as e:
            logger.error("Error deleting processed video file %s: %s", processed_file_path, e, exc_info=True)
            
    # 4. Delete Database Record
    try:
        # Use 'super(type(video), video)' to call the parent's delete method
        super(type(video), video).delete(*args, **kwargs)
        logger.info("Deleted VideoFile database record PK %s (UUID: %s).", video.pk, video.uuid)
        
        return f"Successfully deleted VideoFile {video.uuid} and associated files."
    except Exception as e:
        logger.error("Error deleting VideoFile database record PK %s: %s", video.pk, e, exc_info=True)
        raise # Re-raise the exception


def _get_base_frame_dir(video: "VideoFile") -> Path:
    """Gets the base directory path for storing extracted frames."""
    # Assuming data_paths['frame'] is the root directory for all frame storage
    return data_paths["frame"] / str(video.uuid)


def _set_frame_dir(video: "VideoFile", force_update: bool = False):
    """Sets the frame_dir field based on the video's UUID."""
    target_dir = _get_base_frame_dir(video)
    target_path_str = target_dir.as_posix() # Store as POSIX path string

    if not video.frame_dir or video.frame_dir != target_path_str or force_update:
        video.frame_dir = target_path_str
        logger.info("Set frame_dir for video %s to %s", video.uuid, video.frame_dir)
        # Avoid saving if called from within the save method itself
        if not getattr(video, '_saving', False):
             video.save(update_fields=['frame_dir'])


def _get_frame_dir_path(video: "VideoFile") -> Optional[Path]:
    """Returns the Path object for the frame directory, if set."""
    if not video.frame_dir:
        _set_frame_dir(video)
    
    return Path(video.frame_dir) 

def _get_temp_anonymized_frame_dir(video: "VideoFile") -> Path:
    """Gets the path for the temporary directory used during anonymization frame creation."""
    base_frame_dir = _get_base_frame_dir(video)
    # Place temp dir alongside the final frame dir but with a prefix/suffix
    anon_dir = base_frame_dir.parent / f"anonymizing_{base_frame_dir.name}"
    return anon_dir


def _get_target_anonymized_video_path(video: "VideoFile") -> Path:
    """Determines the target path for the anonymized/processed video file."""
    if not video.has_raw or not video.raw_file.name:
         # If raw is gone, maybe base it on UUID? Requires careful thought.
         # For now, assume raw is needed to determine the original filename base.
         raise ValueError("Cannot determine target anonymized path without a raw file reference.")

    # Use the filename part of the raw file's relative path
    raw_path_relative = Path(video.raw_file.name)
    # Place it in the ANONYM_VIDEO_DIR using the same filename
    return ANONYM_VIDEO_DIR / raw_path_relative.name
