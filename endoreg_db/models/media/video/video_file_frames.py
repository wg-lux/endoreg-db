import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional
from django.conf import settings
from icecream import ic
from tqdm import tqdm
from django.db import transaction

from ....utils.video.ffmpeg_wrapper import extract_frames as ffmpeg_extract_frames
from .video_file_io import _get_frame_dir_path, _get_temp_anonymized_frame_dir


if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState
    from ..frame import Frame
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)

def _get_frame_number(video: "VideoFile") -> int:
    """Counts the number of associated Frame objects in the database."""
    try:
        # Access related manager directly
        return video.frames.count()
    except AttributeError:
        logger.error("Could not access frame count for video %s. 'frames' related manager not found.", str(video))
        ic("Error: 'frames' related manager not found.")
        # Fallback query (less efficient)
        frame_model = video.get_frame_model()
        return frame_model.objects.filter(video_file=video).count()
    except Exception as e:
        logger.error("Error counting frames for video %s: %s", video.uuid, e, exc_info=True)
        return 0


def _get_frames(video: "VideoFile") -> "QuerySet[Frame]":
    """Gets a QuerySet of all associated Frame objects, ordered by frame number."""
    from endoreg_db.models import Frame
    try:
        # Access related manager directly
        return video.frames.order_by("frame_number")
    except AttributeError:
        logger.error("Could not access frames for video %s. 'frames' related manager not found.", video.uuid)
        ic("Error: 'frames' related manager not found.")
        # Fallback query
        return Frame.objects.filter(video_file=video).order_by("frame_number")
    except Exception as e:
        logger.error("Error getting frames for video %s: %s", video.uuid, e, exc_info=True)
        return Frame.objects.none() # Return empty queryset on error


def _get_frame(video: "VideoFile", frame_number: int) -> "Frame":
    """Gets a specific Frame object by its frame number."""
    from endoreg_db.models import Frame
    try:
        # Access related manager directly
        return video.frames.get(frame_number=frame_number)
    except AttributeError:
        logger.error("Could not access frame %d for video %s via related manager.", frame_number, video.uuid)
        ic("Error: 'frames' related manager not found, using direct query.")
        # Fallback query
        return Frame.objects.get(video_file=video, frame_number=frame_number)
    except Frame.DoesNotExist:
        logger.error("Frame %d not found for video %s.", frame_number, video.uuid)
        raise # Re-raise DoesNotExist
    except Exception as e:
        logger.error("Error getting frame %d for video %s: %s", frame_number, video.uuid, e, exc_info=True)
        raise # Re-raise other exceptions


def _get_frame_range(video: "VideoFile", start_frame_number: int, end_frame_number: int) -> "QuerySet[Frame]":
    """Gets a QuerySet of Frame objects within a specific range, ordered by frame number."""
    from endoreg_db.models import Frame
    try:
        # Access related manager directly
        return video.frames.filter(
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")
    except AttributeError:
        logger.error("Could not access frame range for video %s via related manager.", video.uuid)
        ic("Error: 'frames' related manager not found, using direct query.")
        # Fallback query
        return Frame.objects.filter(
            video_file=video,
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")
    except Exception as e:
        logger.error("Error getting frame range (%d-%d) for video %s: %s", start_frame_number, end_frame_number, video.uuid, e, exc_info=True)
        return Frame.objects.none() # Return empty queryset on error

# --- Frame Creation/Deletion ---

def _get_frame_path(video: "VideoFile", frame_number: int) -> Optional[Path]:
    """Constructs the expected path for a given frame number."""
    target_dir = video.get_frame_dir_path() # Use IO helper
    if not target_dir:
        logger.warning("Cannot get frame path for video %s: Frame directory not set.", video.uuid)
        return None

    filename_index = frame_number

    frame_filename = f"frame_{filename_index:07d}.jpg"
    path = target_dir / frame_filename
    return path


# --- Fix _get_frame_paths ---
def _get_frame_paths(video: "VideoFile") -> List[Path]:
    """Returns a sorted list of Path objects for extracted frame image files."""
    frame_dir = _get_frame_dir_path(video)
    if not frame_dir or not frame_dir.exists():
        logger.warning("Frame directory %s does not exist for video %s.", frame_dir, video.uuid)
        return []

    # Use glob to find only expected frame files
    frame_paths = list(frame_dir.glob('frame_*.jpg'))

    # Sort the paths based on the frame number
    try:
        # Add a check within the lambda or filter beforehand for robustness if needed,
        # but glob should prevent non-matching files.
        frame_paths.sort(key=lambda p: int(p.stem.split('_')[-1]))
    except (ValueError, IndexError) as e:
         logger.error("Error sorting frame paths in %s: %s. Found paths: %s", frame_dir, e, [p.name for p in frame_paths], exc_info=True)
         # Decide how to handle: return unsorted, empty list, or raise error?
         # Returning empty might be safest to prevent downstream errors.
         return []
    return frame_paths
# --- End Fix ---

def _create_frame_object(
    video: "VideoFile", frame_number: int, image_file=None, extracted: bool = False
) -> "Frame":
    """Instantiates a Frame object (does not save it)."""
    from endoreg_db.models import Frame
    
    return Frame(
        video=video, # Changed from video_file
        frame_number=frame_number,
        relative_path=image_file, # Changed from image
        is_extracted=extracted, # Changed from extracted
        # Add other default fields if necessary
    )


def _bulk_create_frames(video: "VideoFile", frames_to_create: List["Frame"]):
    """Helper function to perform bulk_create with ignore_conflicts."""
    from endoreg_db.models import Frame # Local import
    try:
        # --- FIX: Use ignore_conflicts=True ---
        Frame.objects.bulk_create(frames_to_create, ignore_conflicts=True)
        # --- End Fix ---
    except Exception as e:
        logger.error("Error during bulk creation of frames for video %s: %s", video.uuid, e, exc_info=True)
        raise # Re-raise to allow handling in _initialize_frames

@transaction.atomic # Keep atomic for frame extraction and state updates
def _extract_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False, # verbose is unused currently
) -> List[Path]:
    """Extracts frames from the raw video file using ffmpeg and initializes Frame objects."""
    if not video.has_raw:
        logger.error("Raw video file not available for %s. Cannot extract frames.", video.uuid)
        raise FileNotFoundError(f"Raw video file not available for {video.uuid}")

    raw_file_path = video.get_raw_file_path() # Use IO helper
    if not raw_file_path or not raw_file_path.exists():
        logger.error("Raw video file not found at %s. Cannot extract frames.", raw_file_path)
        raise FileNotFoundError(f"Raw video file not found at {raw_file_path}")

    frame_dir = _get_frame_dir_path(video) # Use IO helper
    if not frame_dir:
        logger.error("Cannot determine frame directory path for video %s.", video.uuid)
        return False

    # Check if frames already exist and handle overwrite logic
    state = video.get_or_create_state()

    if frame_dir.exists() and state.frames_extracted and not overwrite:
        logger.info("Frames already extracted and overwrite=False, skipping extraction for video %s.", video.uuid)
        # Ensure initialized flag is also checked/set if needed
        if not state.frames_initialized:
             logger.warning("Frames directory exists but state not initialized. Attempting initialization.")
             try:
                 # Find existing frame files to initialize from
                 existing_paths = sorted(list(frame_dir.glob('frame_*.jpg')), key=lambda p: int(p.stem.split('_')[-1]))
                 if existing_paths:
                     _initialize_frames(video, existing_paths)
                 else:
                     logger.error("Frame directory exists but contains no frame files for video %s.", video.uuid)
                     # Consider deleting the empty dir and re-extracting or returning False
                     return False
             except Exception as init_e:
                 logger.error("Failed to initialize existing frames for video %s: %s", video.uuid, init_e, exc_info=True)
                 return False # Fail if initialization fails
        return True # Return True as frames are present


    if frame_dir.exists() and overwrite:
        logger.info("Overwrite=True, deleting existing frames for video %s before extraction.", video.uuid)
        # Call the reverted _delete_frames which only deletes files and resets state
        _delete_frames(video)
        # Re-fetch state as it was modified
        state = video.get_or_create_state()

    # Ensure frame directory exists
    frame_dir.mkdir(parents=True, exist_ok=True)

    
    try:
        logger.info("Starting frame extraction for video %s to %s", video.uuid, frame_dir)
        extracted_paths = ffmpeg_extract_frames(video.get_raw_file_path(), frame_dir, quality = 2)
        if not extracted_paths:
            logger.error("ffmpeg_extract_frames returned no paths for video %s.", video.uuid)
            raise RuntimeError("Frame extraction failed (no paths returned).")
        logger.info("Successfully extracted %d frames using ffmpeg for video %s.", len(extracted_paths), video.uuid)

        # Initialize Frame objects using the modified helper
        _initialize_frames(video, extracted_paths) # This now uses ignore_conflicts

        # Update state after successful extraction and initialization
        state.frames_extracted = True
        # frames_initialized and frame_count are set within _initialize_frames now
        state.save(update_fields=['frames_extracted'])
        logger.info("Set frames_extracted state to True for video %s.", video.uuid)
        return True

    except Exception as e:
        logger.error("Frame extraction or initialization failed for video %s: %s", video.uuid, e, exc_info=True)
        # Attempt cleanup
        logger.warning("Cleaning up frame directory %s due to extraction error.", frame_dir)
        shutil.rmtree(frame_dir, ignore_errors=True)
        # Attempt to reset state, catching potential TransactionManagementError
        try:
            state.frames_extracted = False
            state.frames_initialized = False
            state.frame_count = None
            state.save(update_fields=['frames_extracted', 'frames_initialized', 'frame_count'])
        except Exception as state_e:
            logger.error("Failed to update state after frame extraction error for video %s: %s", video.uuid, state_e, exc_info=True)
        return False


def _initialize_frames(video: "VideoFile", frame_paths: List[Path]):
    """Initializes Frame objects in the database for the given paths."""
    from endoreg_db.models import Frame # Local import

    logger.info("Initializing %d Frame objects for video %s.", len(frame_paths), video.uuid)
    frames_to_create = []
    for frame_path in tqdm(frame_paths, desc=f"Initializing Frames {video.uuid}", unit="frame"):
        try:
            frame_number = int(frame_path.stem.split('_')[-1])
                        # --- FIX: Set relative_path ---
            relative_path_str = frame_path.name # Store just the filename
            frames_to_create.append(
                Frame(
                    video=video,
                    frame_number=frame_number,
                    relative_path=relative_path_str, # Store filename
                    is_extracted=True # Mark as extracted since the file exists
                )
            )
            # --- End Fix ---
        except (ValueError, IndexError) as e:
            logger.warning("Could not parse frame number from %s: %s", frame_path.name, e)
            continue # Skip this frame

    if frames_to_create:
        try:
            _bulk_create_frames(video, frames_to_create) # Call helper
            logger.info("Successfully initialized %d Frame objects for video %s.", len(frames_to_create), video.uuid)
            # Update state after successful initialization
            try:
                state = video.get_or_create_state()
                state.frames_initialized = True
                state.frame_count = len(frames_to_create) # Set frame count based on created objects
                state.save(update_fields=['frames_initialized', 'frame_count'])
            except Exception as state_e:
                 logger.error("Failed to update state after frame initialization for video %s: %s", video.uuid, state_e, exc_info=True)

        except Exception as e:
            logger.error("Error initializing frames for video %s: %s", video.uuid, e, exc_info=True)
            raise # Re-raise the exception to be caught by _extract_frames

    else:
        logger.warning("No valid frame paths found to initialize for video %s.", video.uuid)



@transaction.atomic # Keep atomic for state updates
def _delete_frames(video: "VideoFile") -> str:
    """
    Deletes extracted frame FILES ONLY (not DB objects).
    Resets relevant state flags.
    Also cleans up temporary anonymization frame directories.
    """
    deleted_messages = []
    error_messages = []
    state_updated = False

    # 1. Delete frame directory
    frame_dir = _get_frame_dir_path(video)
    if frame_dir and frame_dir.exists():
        try:
            shutil.rmtree(frame_dir)
            msg = f"Deleted frame directory: {frame_dir}"
            logger.info(msg)
            deleted_messages.append(msg)
        except Exception as e:
            msg = f"Error deleting frame directory {frame_dir}: {e}"
            logger.error(msg, exc_info=True)
            error_messages.append(msg)
    elif frame_dir:
        msg = f"Frame directory not found, skipping deletion: {frame_dir}"
        logger.debug(msg)
    else:
        msg = f"Frame directory path not set for video {video.uuid}, cannot delete standard frames."
        logger.warning(msg)


    # 2. Delete temporary anonymized frame directory (if exists)
    try:
        temp_anonym_frame_dir = _get_temp_anonymized_frame_dir(video) # Use IO helper
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            shutil.rmtree(temp_anonym_frame_dir)
            msg = f"Deleted temporary anonymized frame directory: {temp_anonym_frame_dir}"
            logger.info(msg)
            deleted_messages.append(msg)
    except Exception as e:
        msg = f"Error deleting temporary anonymized frame directory {temp_anonym_frame_dir}: {e}"
        logger.error(msg, exc_info=True)
        error_messages.append(msg)

    # 3. Update state and frame count (Do NOT delete Frame DB objects)
    try:
        state: "VideoState" = video.get_or_create_state()
        state.frames_extracted = False
        state.frames_initialized = False # Reset initialized flag as well
        # Keep state.frame_count as it reflects DB objects which still exist
        state.save(update_fields=['frames_extracted', 'frames_initialized'])
        logger.info("Reset frame state flags (frames_extracted, frames_initialized) for video %s.", video.uuid)
        state_updated = True
    except Exception as state_e:
        msg = f"Failed to update state after deleting frame files for video %s: {state_e}"
        logger.error(msg, exc_info=True)
        error_messages.append(msg)

    # Construct final message
    final_message = "; ".join(deleted_messages)
    if error_messages:
        final_message += "; Errors occurred: " + "; ".join(error_messages)
    elif state_updated:
        final_message += "; State flags updated successfully."
    else:
        final_message += "; State flag update skipped due to errors."

    # Do not raise error just for file deletion failure, but log it.
    # Only critical DB errors (which shouldn't happen here now) would warrant raising.

    return final_message