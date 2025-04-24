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
from .video_file_io import _get_frame_dir_path


if TYPE_CHECKING:
    from .video_file import VideoFile
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
    try:
        # Access related manager directly
        return video.frames.order_by("frame_number")
    except AttributeError:
        logger.error("Could not access frames for video %s. 'frames' related manager not found.", video.uuid)
        ic("Error: 'frames' related manager not found.")
        # Fallback query
        frame_model = video.get_frame_model()
        return frame_model.objects.filter(video_file=video).order_by("frame_number")
    except Exception as e:
        logger.error("Error getting frames for video %s: %s", video.uuid, e, exc_info=True)
        frame_model = video.get_frame_model()
        return frame_model.objects.none() # Return empty queryset on error


def _get_frame(video: "VideoFile", frame_number: int) -> "Frame":
    """Gets a specific Frame object by its frame number."""
    frame_model = video.get_frame_model()
    try:
        # Access related manager directly
        return video.frames.get(frame_number=frame_number)
    except AttributeError:
         logger.error("Could not access frame %d for video %s via related manager.", frame_number, video.uuid)
         ic("Error: 'frames' related manager not found, using direct query.")
         # Fallback query
         return frame_model.objects.get(video_file=video, frame_number=frame_number)
    except frame_model.DoesNotExist:
         logger.error("Frame %d not found for video %s.", frame_number, video.uuid)
         raise # Re-raise DoesNotExist
    except Exception as e:
        logger.error("Error getting frame %d for video %s: %s", frame_number, video.uuid, e, exc_info=True)
        raise # Re-raise other exceptions


def _get_frame_range(video: "VideoFile", start_frame_number: int, end_frame_number: int) -> "QuerySet[Frame]":
    """Gets a QuerySet of Frame objects within a specific range, ordered by frame number."""
    frame_model = video.get_frame_model()
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
        return frame_model.objects.filter(
            video_file=video,
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")
    except Exception as e:
        logger.error("Error getting frame range (%d-%d) for video %s: %s", start_frame_number, end_frame_number, video.uuid, e, exc_info=True)
        return frame_model.objects.none() # Return empty queryset on error

# --- Frame Creation/Deletion ---

def _get_frame_path(video: "VideoFile", frame_number: int) -> Optional[Path]:
    """Constructs the expected path for a given frame number."""
    target_dir = video.get_frame_dir_path() # Use IO helper
    if not target_dir:
        logger.warning("Cannot get frame path for video %s: Frame directory not set.", video.uuid)
        return None

    # Frame filenames are typically 1-based index, e.g., frame_0000001.jpg for frame_number 0
    # Adjust index based on actual naming convention used by extract_frames
    # Assuming 1-based index in filename matching frame_number + 1
    filename_index = frame_number + 1
    # TODO: Make extension configurable or detect from existing files? Defaulting to jpg.
    frame_filename = f"frame_{filename_index:07d}.jpg"
    path = target_dir / frame_filename

    return path


def _get_frame_paths(video: "VideoFile") -> List[Path]:
    """
    Retrieves the file paths of all extracted frames associated with the video.

    Args:
        video: The VideoFile instance.

    Returns:
        A list of Path objects representing the frame file paths, sorted by frame number.
        Returns an empty list if frames are not extracted or an error occurs.
    """
    if not video.state or not video.state.frames_extracted:
        logger.warning("Frames not extracted for video %s. Cannot get frame paths.", video.uuid)
        return []

    # Get the base storage path (MEDIA_ROOT)
    try:
        storage_base_path = Path(settings.MEDIA_ROOT)
        if not storage_base_path.is_absolute():
            storage_base_path = (settings.BASE_DIR / storage_base_path).resolve()
    except AttributeError:
        logger.error("Could not determine storage base path (settings.MEDIA_ROOT). Cannot construct absolute frame paths.")
        return []

    try:
        # Filter frames that are marked as extracted
        extracted_frames = _get_frames(video).filter(is_extracted=True) # Use local helper
        # Order by frame number to ensure correct sequence
        extracted_frames = extracted_frames.order_by('frame_number')
        # Construct absolute paths by joining MEDIA_ROOT and the relative path
        frame_paths = [storage_base_path / frame.relative_path for frame in extracted_frames]
        return frame_paths
    except Exception as e:
        logger.error("Could not get frame paths for video %s: %s", video.uuid, e, exc_info=True)
        return []


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
    """Bulk creates Frame objects in the database."""
    from endoreg_db.models import Frame
    if not frames_to_create:
        return
    try:
        Frame.objects.bulk_create(frames_to_create)
        logger.debug("Bulk created %d Frame objects for video %s.", len(frames_to_create), video.uuid)
    except Exception as e:
        logger.error("Error during bulk creation of frames for video %s: %s", video.uuid, e, exc_info=True)
        raise

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

    frame_dir_path = video.get_frame_dir_path() # Use IO helper
    if not frame_dir_path:
        logger.error("Frame directory path is not set for video %s. Cannot extract frames.", video.uuid)
        raise ValueError(f"Frame directory not set for {video.uuid}")

    # Check if frames already exist and handle overwrite logic
    state = video.get_or_create_state()
    if state.frames_extracted and not overwrite:
        logger.warning("Frames already extracted for video %s and overwrite=False. Skipping extraction.", video.uuid)
        # Return existing paths if needed, or an empty list/None
        return video.get_frame_paths() # Return existing paths

    if state.frames_extracted and overwrite:
        logger.warning("Frames already extracted for video %s but overwrite=True. Deleting existing frames first.", video.uuid)
        video.delete_frames() # Delete existing frames and DB entries

    # Ensure frame directory exists
    frame_dir_path.mkdir(parents=True, exist_ok=True)
    logger.info("Starting frame extraction for video %s to %s", video.uuid, frame_dir_path)

    extracted_paths: List[Path] = []
    try:
        # Call the ffmpeg_wrapper utility function directly
        extracted_paths = ffmpeg_extract_frames(
            video_path=raw_file_path, # Pass the path to the utility
            output_dir=frame_dir_path, # Pass the output dir
            quality=quality,
            ext=ext,
            # fps=video.fps # Optionally pass fps if needed for specific extraction rate
        )

        if not extracted_paths:
             # ffmpeg_extract_frames might return empty on error, check logs
             logger.error("Frame extraction using ffmpeg failed or produced no files for video %s.", video.uuid)
             raise RuntimeError(f"Frame extraction failed for {video.uuid}")

        logger.info("Successfully extracted %d frames using ffmpeg for video %s.", len(extracted_paths), video.uuid)

        # Update state and initialize Frame objects
        state.frames_extracted = True
        state.save(update_fields=['frames_extracted'])

        # Initialize Frame DB Objects using the logic moved from the utility
        _initialize_frames(video, extracted_paths) # Call local helper

        return extracted_paths

    except Exception as e:
        logger.error("Frame extraction or initialization failed for video %s: %s", video.uuid, e, exc_info=True)
        # Update state on failure
        try:
            # Ensure state reflects failure
            state.frames_extracted = False
            state.frames_initialized = False # Also mark initialization as failed
            state.save(update_fields=['frames_extracted', 'frames_initialized'])
        except Exception as state_e:
            logger.error("Failed to update state after frame extraction error for video %s: %s", video.uuid, state_e, exc_info=True)
        # Clean up potentially created frame files if extraction failed mid-way
        if frame_dir_path.exists():
             logger.warning("Cleaning up frame directory %s due to extraction error.", frame_dir_path)
             shutil.rmtree(frame_dir_path, ignore_errors=True)
        raise # Re-raise the original exception



def _initialize_frames(video: "VideoFile", extracted_paths: List[Path]):
    """
    Initializes Frame database objects based on extracted frame file paths.
    (Logic moved from utils.video.extract_frames.initialize_frame_objects)
    """
    state = video.get_or_create_state()
    # Check state before proceeding
    if state.frames_initialized:
        ic(f"Frames already initialized for video {video.uuid}, skipping.")
        logger.warning("Frames already initialized for video %s, skipping initialization.", video.uuid)
        return

    if not extracted_paths:
        ic(f"No extracted paths provided for video {video.uuid}, cannot initialize frames.")
        logger.warning("No extracted paths provided for video %s, cannot initialize frames.", video.uuid)
        # Mark as not initialized if called with no paths? Or just return?
        # Let's assume if _extract_frames succeeded, paths should exist.
        # If called independently, this might need error handling.
        return

    logger.info("Initializing %d Frame objects for video %s.", len(extracted_paths), video.uuid)

    try:
        video.frame_count = len(extracted_paths)
        frames_to_create = []
        # Consider making batch size configurable via settings or env var
        batch_size = int(os.environ.get("DJANGO_FRAME_INIT_BATCHSIZE", "500"))

        # Prepare frame data (relative paths for storage)
        frame_dir = video.get_frame_dir_path() # Should be the parent of extracted_paths
        if not frame_dir:
             raise ValueError(f"Frame directory not set for video {video.uuid}")

        # Get storage root (MEDIA_ROOT typically)
        # Be careful with storage backend details. Assuming default FileSystemStorage here.
        # If using S3 etc., getting the relative path might need storage-specific methods.
        try:
            # Accessing storage location directly might be fragile.
            # Consider storing relative paths directly if possible during extraction,
            # or construct them based on MEDIA_ROOT setting.
            storage_base_path = Path(settings.MEDIA_ROOT) # Use Django settings
            # Ensure storage_base_path is absolute
            if not storage_base_path.is_absolute():
                 # Handle relative MEDIA_ROOT if necessary, e.g., relative to BASE_DIR
                 storage_base_path = (settings.BASE_DIR / storage_base_path).resolve()

        except AttributeError:
             logger.error("Could not determine storage base path (settings.MEDIA_ROOT). Cannot calculate relative paths.")
             raise ValueError("Storage base path configuration error.")


        for i, path in tqdm(enumerate(extracted_paths, start=0), desc=f"Initializing Frames {video.uuid}", total=len(extracted_paths)):
            try:
                # Extract frame number from filename (e.g., frame_0000001.jpg -> 1)
                # Adjust parsing based on actual filename format from ffmpeg_extract_frames
                frame_number = int(path.stem.split('_')[-1]) -1 # Assuming ffmpeg starts at 1
            except (ValueError, IndexError):
                 logger.error(f"Could not parse frame number from filename: {path.name}. Skipping frame.")
                 continue # Skip this frame

            try:
                # Calculate path relative to MEDIA_ROOT for storage in FileField
                relative_path = path.relative_to(storage_base_path).as_posix()
            except ValueError:
                 logger.error(f"Extracted path {path} is not inside the storage base {storage_base_path}. Cannot determine relative path.")
                 # Decide how to handle: skip, error out, or store absolute? Storing absolute is usually bad.
                 logger.warning(f"Skipping frame {frame_number} due to path issue.")
                 continue


            # Create Frame instance (without saving yet) using the local helper
            frame_obj_instance = _create_frame_object(
                video, frame_number=frame_number, image_file=relative_path, extracted=True
            )
            frames_to_create.append(frame_obj_instance)

            # Bulk create in batches
            # Check index i+1 because enumerate starts at 0
            if (i + 1) % batch_size == 0:
                with transaction.atomic():
                    _bulk_create_frames(video, frames_to_create) # Use local helper
                frames_to_create.clear()

        # Create any remaining frames
        if frames_to_create:
            with transaction.atomic():
                _bulk_create_frames(video, frames_to_create) # Use local helper

        # Update state and save VideoFile (to save frame_count)
        state.frames_initialized = True
        state.save(update_fields=['frames_initialized'])
        video.save(update_fields=['frame_count']) # Save frame_count on VideoFile

        logger.info("Successfully initialized Frame objects and updated frame count for video %s.", video.uuid)

    except Exception as e:
        logger.error("Failed to initialize Frame objects for video %s: %s", video.uuid, e, exc_info=True)
        # Update state on failure
        try:
            state.frames_initialized = False
            # Reset frame count? Maybe not, extraction might have finished but init failed.
            # video.frame_count = None
            state.save(update_fields=['frames_initialized'])
            # video.save(update_fields=['frame_count'])
        except Exception as state_e:
            logger.error("Failed to update state after frame initialization error for video: %s", state_e, exc_info=True)
        # Re-raise the original exception after attempting state update
        raise e

def _delete_frames(video: "VideoFile") -> str:
    """Deletes extracted frame files and associated Frame database objects."""
    from endoreg_db.models import Frame
    deleted_messages = []
    error_messages = []
    state_updated = False

    # 1. Delete frame directory
    frame_dir = _get_frame_dir_path(video) # Use IO helper - Keep using _get_frame_dir_path here for deletion
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
        # If dir doesn't exist, frames likely aren't there. Still proceed to DB cleanup.
    else:
        msg = f"Frame directory path not set for video {video.uuid}, cannot delete standard frames."
        logger.warning(msg)
        # Cannot delete files, but still try to delete DB entries.

    # 2. Delete temporary anonymized frame directory (if exists) - Good practice to include here
    try:
        temp_anonym_frame_dir = video.get_temp_anonymized_frame_dir() # Use IO helper
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            shutil.rmtree(temp_anonym_frame_dir)
            msg = f"Deleted temporary anonymized frame directory: {temp_anonym_frame_dir}"
            logger.info(msg)
            deleted_messages.append(msg)
    except Exception as e:
        msg = f"Error deleting temporary anonymized frame directory {temp_anonym_frame_dir}: {e}"
        logger.error(msg, exc_info=True)
        error_messages.append(msg)

    # 3. Delete Frame objects from database
    try:
        # Access related manager directly or use the Frame model
        # frame_model = video.get_frame_model() # No need to get model dynamically
        # Use the Frame model directly to filter and delete
        count, _ = Frame.objects.filter(video=video).delete() # Use 'video' field
        # Alternative using related manager if available and correctly set up:
        # count, _ = video.frames.all().delete() # Assumes related_name='frames' works
        if count > 0:
            msg = f"Deleted {count} Frame objects from database for video {video.uuid}."
            logger.info(msg)
            deleted_messages.append(msg)
    except AttributeError:
        # This error might occur if the related manager 'frames' isn't properly set up or accessed
        logger.error("Could not delete Frame objects for video %s using related manager. Trying direct model filter.", video.uuid)
        # Fallback to direct model filter if related manager fails
        try:
            count, _ = Frame.objects.filter(video=video).delete()
            if count > 0:
                msg = f"Deleted {count} Frame objects from database for video {video.uuid} (using direct filter)."
                logger.info(msg)
                deleted_messages.append(msg)
        except Exception as e_fallback:
            msg = f"Error deleting Frame objects from database for video {video.uuid} (fallback attempt): {e_fallback}"
            logger.error(msg, exc_info=True)
            error_messages.append(msg)

    except Exception as e:
         msg = f"Error deleting Frame objects from database for video {video.uuid}: {e}"
         logger.error(msg, exc_info=True)
         error_messages.append(msg)

    # 4. Update state and frame count if deletion seemed successful (no errors)
    final_message = "; ".join(deleted_messages)
    if not error_messages:
        try:
            state = video.get_or_create_state()
            state.frames_extracted = False
            state.frames_initialized = False # Also reset initialized state
            state.save(update_fields=['frames_extracted', 'frames_initialized'])
            logger.info("Reset frame state flags to False for video %s.", video.uuid)
            # Reset frame count on VideoFile model
            video.frame_count = None
            video.save(update_fields=['frame_count'])
            logger.info("Reset frame count to None for video %s.", video.uuid)
            state_updated = True
        except Exception as state_e:
            msg = f"Failed to update state/frame count after deleting frames for video %s: {state_e}"
            logger.error(msg, exc_info=True)
            error_messages.append(msg) # Add state update error to messages

    # Construct final message
    if error_messages:
        final_message += "; Errors occurred: " + "; ".join(error_messages)
    elif state_updated:
         final_message += "; State and frame count updated successfully."

    return final_message