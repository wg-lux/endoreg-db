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
         logger.warning("Falling back to unsorted frame paths to preserve available data.")
         return frame_paths
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
) -> bool: # Changed return type to bool
    """
    Extracts frames from the raw video file using ffmpeg, initializes Frame objects,
    and updates relevant VideoState fields atomically.

    Returns:
        bool: True if extraction and initialization were successful, False otherwise.

    State Transitions:
        - On Success: Sets state.frames_extracted=True, state.frames_initialized=True, state.frame_count=N.
        - On Failure: Resets state.frames_extracted=False, state.frames_initialized=False, state.frame_count=None.
    """
    if not video.has_raw:
        logger.error("Raw video file not available for %s. Cannot extract frames.", video.uuid)
        # No state change needed as pre-condition failed
        return False # Indicate failure

    raw_file_path = video.get_raw_file_path() # Use IO helper
    if not raw_file_path or not raw_file_path.exists():
        logger.error("Raw video file not found at %s. Cannot extract frames.", raw_file_path)
        # No state change needed
        return False # Indicate failure

    frame_dir = _get_frame_dir_path(video) # Use IO helper
    if not frame_dir:
        logger.error("Cannot determine frame directory path for video %s.", video.uuid)
        # No state change needed
        return False # Indicate failure

    # Check state and handle overwrite logic
    state = video.get_or_create_state()

    # Pre-condition check: Already extracted and not overwriting?
    if state.frames_extracted and state.frames_initialized and not overwrite:
        logger.info("Frames already extracted and initialized, and overwrite=False. Skipping extraction for video %s.", video.uuid)
        return True # Indicate success (already done)

    # Handle existing directory/state based on overwrite flag
    if frame_dir.exists():
        if overwrite:
            logger.info("Overwrite=True, deleting existing frame files for video %s before extraction.", video.uuid)
            # Call _delete_frames which resets state flags within its own transaction
            _delete_frames(video)
            # Re-fetch state as it was modified
            state = video.get_or_create_state()
        elif state.frames_extracted and not state.frames_initialized:
            # Dir exists, state says extracted but not initialized - try initializing
            logger.warning("Frames directory exists and state.frames_extracted=True, but state.frames_initialized=False. Attempting initialization.")
            try:
                existing_paths = sorted(list(frame_dir.glob(f'frame_*.{ext}')), key=lambda p: int(p.stem.split('_')[-1]))
                if existing_paths:
                    # _initialize_frames handles setting initialized state and count
                    _initialize_frames(video, existing_paths)
                    # Explicitly set frames_extracted just in case it wasn't set before
                    if not state.frames_extracted:
                        state.frames_extracted = True
                        state.save(update_fields=['frames_extracted'])
                    return True # Initialization successful
                else:
                    logger.error("Frame directory exists but contains no frame files for video %s. Extraction needed.", video.uuid)
                    # Proceed to extraction after cleaning the empty dir
                    shutil.rmtree(frame_dir, ignore_errors=True)
            except Exception as init_e:
                logger.error("Failed to initialize existing frames for video %s: %s", video.uuid, init_e, exc_info=True)
                # Reset state on initialization failure
                state.frames_extracted = False
                state.frames_initialized = False
                state.frame_count = None
                state.save(update_fields=['frames_extracted', 'frames_initialized', 'frame_count'])
                return False # Fail if initialization fails
        elif not state.frames_extracted:
             # Dir exists but state says not extracted. This is inconsistent.
             logger.warning("Frame directory %s exists but state.frames_extracted is False. Assuming re-extraction is needed.", frame_dir)
             _delete_frames(video) # Clean up and reset state
             state = video.get_or_create_state() # Refresh state

    # Ensure frame directory exists for extraction
    frame_dir.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("Starting frame extraction for video %s to %s", video.uuid, frame_dir)
        extracted_paths = ffmpeg_extract_frames(raw_file_path, frame_dir, quality=quality, ext=ext)
        if not extracted_paths:
            logger.error("ffmpeg_extract_frames returned no paths for video %s.", video.uuid)
            raise RuntimeError("Frame extraction failed (no paths returned).")
        logger.info("Successfully extracted %d frames using ffmpeg for video %s.", len(extracted_paths), video.uuid)

        # Initialize Frame objects and update state.frames_initialized, state.frame_count
        _initialize_frames(video, extracted_paths) # This now uses ignore_conflicts and sets state

        # Update state.frames_extracted after successful extraction and initialization
        # Refresh state object in case _initialize_frames modified it
        state.refresh_from_db()
        if not state.frames_extracted: # Only update if not already true
            state.frames_extracted = True
            state.save(update_fields=['frames_extracted'])
            logger.info("Set frames_extracted state to True for video %s.", video.uuid)
        return True # Indicate success

    except Exception as e:
        logger.error("Frame extraction or initialization failed for video %s: %s", video.uuid, e, exc_info=True)
        # Attempt cleanup of files
        logger.warning("Cleaning up frame directory %s due to extraction error.", frame_dir)
        shutil.rmtree(frame_dir, ignore_errors=True)
        # Reset state atomically
        state.frames_extracted = False
        state.frames_initialized = False
        state.frame_count = None
        state.save(update_fields=['frames_extracted', 'frames_initialized', 'frame_count'])
        return False # Indicate failure


def _initialize_frames(video: "VideoFile", frame_paths: List[Path]):
    """
    Initializes Frame objects in the database for the given paths and updates state.

    State Transitions:
        - On Success: Sets state.frames_initialized=True, state.frame_count=N.
        - On Failure: Does not change state (error is raised).
    """
    from endoreg_db.models import Frame # Local import

    if not frame_paths:
        logger.warning("No frame paths provided to initialize for video %s.", video.uuid)
        # Ensure state reflects no initialization if no paths
        try:
            state = video.get_or_create_state()
            if state.frames_initialized or state.frame_count is not None:
                state.frames_initialized = False
                state.frame_count = None
                state.save(update_fields=['frames_initialized', 'frame_count'])
        except Exception as state_e:
            logger.error("Failed to reset state during empty initialization for video %s: %s", video.uuid, state_e, exc_info=True)
        return # Nothing to do

    logger.info("Initializing %d Frame objects for video %s.", len(frame_paths), video.uuid)
    frames_to_create = []
    for frame_path in tqdm(frame_paths, desc=f"Initializing Frames {video.uuid}", unit="frame"):
        try:
            frame_number = int(frame_path.stem.split('_')[-1])
            relative_path_str = frame_path.name # Store just the filename
            frames_to_create.append(
                Frame(
                    video=video,
                    frame_number=frame_number,
                    relative_path=relative_path_str, # Store filename
                    is_extracted=True # Mark as extracted since the file exists
                )
            )
        except (ValueError, IndexError) as e:
            logger.warning("Could not parse frame number from %s: %s", frame_path.name, e)
            continue # Skip this frame

    if frames_to_create:
        try:
            _bulk_create_frames(video, frames_to_create) # Call helper
            num_created = len(frames_to_create)
            logger.info("Successfully initialized %d Frame objects for video %s.", num_created, video.uuid)
            # Update state after successful initialization
            try:
                state = video.get_or_create_state()
                state.frames_initialized = True
                state.frame_count = num_created # Set frame count based on created objects
                state.save(update_fields=['frames_initialized', 'frame_count'])
                logger.info("Set frames_initialized=True and frame_count=%d for video %s.", num_created, video.uuid)
            except Exception as state_e:
                 logger.error("Failed to update state after frame initialization for video %s: %s", video.uuid, state_e, exc_info=True)
                 # Raise error to ensure transaction rollback if state update fails
                 raise RuntimeError("Failed to update state after frame initialization") from state_e

        except Exception as e:
            logger.error("Error initializing frames for video %s: %s", video.uuid, e, exc_info=True)
            # Do not update state here, let the exception propagate to _extract_frames
            raise # Re-raise the exception

    else:
        logger.warning("No valid frame paths found to initialize for video %s.", video.uuid)
        # Ensure state reflects no initialization
        try:
            state = video.get_or_create_state()
            if state.frames_initialized or state.frame_count is not None:
                state.frames_initialized = False
                state.frame_count = None
                state.save(update_fields=['frames_initialized', 'frame_count'])
        except Exception as state_e:
            logger.error("Failed to reset state during empty initialization for video %s: %s", video.uuid, state_e, exc_info=True)


@transaction.atomic # Keep atomic for state updates
def _delete_frames(video: "VideoFile") -> str:
    """
    Deletes extracted frame FILES ONLY (not DB objects).
    Resets relevant state flags atomically.
    Also cleans up temporary anonymization frame directories.

    State Transitions:
        - Sets state.frames_extracted=False, state.frames_initialized=False.
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
        update_fields_state = []
        if state.frames_extracted:
            state.frames_extracted = False
            update_fields_state.append('frames_extracted')
        if state.frames_initialized:
            state.frames_initialized = False
            update_fields_state.append('frames_initialized')
        # Keep state.frame_count as it reflects DB objects which still exist

        if update_fields_state:
            state.save(update_fields=update_fields_state)
            logger.info("Reset frame state flags (%s) for video %s.", ", ".join(update_fields_state), video.uuid)
            state_updated = True
        else:
             logger.info("Frame state flags already False for video %s.", video.uuid)
             state_updated = True # Consider it updated if already correct

    except Exception as state_e:
        msg = f"Failed to update state after deleting frame files for video %s: {state_e}"
        logger.error(msg, exc_info=True)
        error_messages.append(msg)
        # Raise error to ensure transaction rollback if state update fails
        raise RuntimeError("Failed to update state during frame file deletion") from state_e

    # Construct final message
    final_message = "; ".join(deleted_messages)
    if error_messages:
        final_message += "; Errors occurred: " + "; ".join(error_messages)
    elif state_updated:
        final_message += "; State flags updated successfully."
    else:
        final_message += "; State flag update skipped due to errors."

    return final_message