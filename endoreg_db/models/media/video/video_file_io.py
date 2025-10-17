import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from django.db import transaction

from ...utils import data_paths, ANONYM_VIDEO_DIR, VIDEO_DIR # Import VIDEO_DIR for correct path resolution

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger("video_file")

def _get_raw_file_path(video: "VideoFile") -> Optional[Path]:
    """
    Resolves and returns the absolute path to the raw video file if available.
    The FileField stores a path relative to the storage root. We need to join
    that relative path onto the actual video directory under STORAGE_DIR.
    """
    try:
        if video.has_raw and video.raw_file.name:
            # raw_file.name is a relative storage path like 'videos/<filename>'
            raw_rel = Path(video.raw_file.name)
            
            # If it already contains the video directory name, keep the tail
            rel_name = raw_rel.name if raw_rel.parent.name == VIDEO_DIR.name else raw_rel
            full_path = data_paths["video"] / rel_name
            
            # If primary path doesn't exist, check alternative locations
            if not full_path.exists():
                # Check if file is in sensitive subdirectory
                sensitive_path = data_paths["video"] / "sensitive" / rel_name
                if sensitive_path.exists():
                    return sensitive_path.resolve()
                
                # Check direct raw_file.path if available
                # Check direct raw_file.path if available
                try:
                    direct_path = Path(video.raw_file.path)
                    if direct_path.exists():
                        return direct_path.resolve()
                except Exception as e:
                    logger.debug("Could not access direct raw_file.path for video %s: %s", video.uuid, e)
                    # Fallback to checking alternative paths
                
                # Check common alternative paths
                alternative_paths = [
                    Path("/home/admin/dev/lx-annotate/libs/data/videos") / rel_name,
                    Path("/home/admin/dev/lx-annotate/libs/data/videos/sensitive") / rel_name,
                    data_paths["video"].parent / "libs" / "data" / "videos" / rel_name,
                ]
                
                for alt_path in alternative_paths:
                    if alt_path.exists():
                        return alt_path.resolve()
            
            return full_path.resolve()
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

@transaction.atomic
def _delete_with_file(video: "VideoFile", *args, **kwargs):
    """Deletes the VideoFile record and its associated physical files (raw, processed, frames)."""
    # 1. Delete Frames (using the frame helper function via instance method)
    try:
        # delete_frames raises RuntimeError on state update failure
        frame_delete_msg = video.delete_frames()
        logger.info("Frame deletion result for video %s: %s", video.uuid, frame_delete_msg)
    except Exception as frame_del_e:
        # Log error but continue, as file deletion might still be possible
        logger.error("Error during frame file/state deletion for video %s: %s", video.uuid, frame_del_e, exc_info=True)

    # 2. Delete Raw File
    raw_file_path = _get_raw_file_path(video)
    if raw_file_path:
        try:
            if raw_file_path.exists():
                raw_file_path.unlink()
                logger.info("Deleted raw video file for %s: %s", video.uuid, raw_file_path)
            else:
                logger.warning("Raw video file not found at %s for video %s, skipping deletion.", raw_file_path, video.uuid)

        except Exception as e:
            # Log error but continue
            logger.error("Error deleting raw video file %s for video %s: %s", raw_file_path, video.uuid, e, exc_info=True)

    # 3. Delete Processed File
    processed_file_path = _get_processed_file_path(video)
    if processed_file_path:
        try:
            if processed_file_path.exists():
                processed_file_path.unlink()
                logger.info("Deleted processed video file for %s: %s", video.uuid, processed_file_path)
            else:
                logger.warning("Processed video file not found at %s for video %s, skipping deletion.", processed_file_path, video.uuid)
        except Exception as e:
            # Log error but continue
            logger.error("Error deleting processed video file %s for video %s: %s", processed_file_path, video.uuid, e, exc_info=True)

    # 4. Delete Database Record
    try:
        # Use 'super(type(video), video)' to call the parent's delete method
        super(type(video), video).delete(*args, **kwargs)
        logger.info("Deleted VideoFile database record PK %s (UUID: %s).", video.pk, video.uuid)

        return f"Successfully deleted VideoFile {video.uuid} and attempted file cleanup."
    except Exception as e:
        logger.error("Error deleting VideoFile database record PK %s (UUID: %s): %s", video.pk, video.uuid, e, exc_info=True)
        raise # Re-raise the exception for DB deletion failure

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
