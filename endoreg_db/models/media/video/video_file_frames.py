import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional
from icecream import ic

# Assuming utils.video contains extract_frames and initialize_frame_objects
from ....utils.video import extract_frames as util_extract_frames
from ....utils.video import initialize_frame_objects as util_initialize_frame_objects

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


def _extract_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False,
) -> List[Path]:
    """Extracts frames from the raw video file."""
    if not video.has_raw:
        logger.error("Raw video file not available for %s. Cannot extract frames.", video.uuid)
        raise FileNotFoundError(f"Raw video file not available for {video.uuid}")

    raw_file_path = video.get_raw_file_path() # Use helper
    if not raw_file_path or not raw_file_path.exists():
         logger.error("Raw video file not found at %s. Cannot extract frames.", raw_file_path)
         raise FileNotFoundError(f"Raw video file not found at {raw_file_path}")

    frame_dir_path = video.get_frame_dir_path() # Use IO helper
    if not frame_dir_path:
        logger.error("Frame directory path is not set for video %s. Cannot extract frames.", video.uuid)
        raise ValueError(f"Frame directory not set for {video.uuid}")

    logger.info("Starting frame extraction for video %s to %s", video.uuid, frame_dir_path)
    ic(f"Starting frame extraction for {video.raw_file.name} to {frame_dir_path}")

    try:
        # Call the utility function
        extracted_paths = util_extract_frames(
            video=video, # Pass the VideoFile instance itself
            quality=quality,
            overwrite=overwrite,
            ext=ext,
            verbose=verbose,
        )
        logger.info("Successfully extracted %d frames for video %s.", len(extracted_paths), video.uuid)
        ic(f"Extracted {len(extracted_paths)} frames.")

        # Update state and initialize Frame objects
        video.set_frames_extracted(True) # Use State helper via instance method
        _initialize_frames(video, extracted_paths) # Call local helper

        return extracted_paths

    except Exception as e:
        logger.error("Frame extraction failed for video %s: %s", video.uuid, e, exc_info=True)
        ic(f"Frame extraction failed: {e}")
        video.set_frames_extracted(False) # Use State helper via instance method
        raise


def _initialize_frames(video: "VideoFile", paths: List[Path]):
    """Initializes Frame database objects based on extracted frame file paths."""
    if not paths:
        logger.warning("No frame paths provided for initialization for video %s.", video.uuid)
        ic("No frame paths provided for initialization.")
        return

    logger.info("Initializing %d Frame objects for video %s.", len(paths), video.uuid)
    ic(f"Initializing {len(paths)} Frame objects.")
    try:
        # Call the utility function
        util_initialize_frame_objects(video, paths)
        logger.info("Successfully initialized Frame objects for video %s.", video.uuid)
        ic("Frame objects initialized.")
    except Exception as e:
        logger.error("Failed to initialize Frame objects for video %s: %s", video.uuid, e, exc_info=True)
        ic(f"Failed to initialize Frame objects: {e}")
        # Optionally, set frames_extracted state back to False?
        # video.set_frames_extracted(False)


def _delete_frames(video: "VideoFile") -> str:
    """Deletes extracted frame files and associated Frame database objects."""
    deleted_messages = []
    error_messages = []

    # 1. Delete frame directory
    frame_dir = video.get_frame_dir_path() # Use IO helper
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
    temp_anonym_frame_dir = video._get_temp_anonymized_frame_dir() # Use IO helper
    if temp_anonym_frame_dir.exists():
        try:
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
        # Access related manager directly
        count, _ = video.frames.all().delete()
        if count > 0:
            msg = f"Deleted {count} Frame objects from database for video {video.uuid}."
            logger.info(msg)
            deleted_messages.append(msg)
    except AttributeError:
         logger.error("Could not delete Frame objects for video %s. 'frames' related manager not found.", video.uuid)
         error_messages.append("Could not delete Frame objects due to missing related manager.")
    except Exception as e:
         msg = f"Error deleting Frame objects from database for video {video.uuid}: {e}"
         logger.error(msg, exc_info=True)
         error_messages.append(msg)

    # 4. Update state if successful
    final_message = "; ".join(deleted_messages)
    if not error_messages:
        video.set_frames_extracted(False) # Use State helper via instance method
        logger.info("Reset state.frames_extracted to False for video %s.", video.uuid)
    else:
        final_message += "; Errors occurred: " + "; ".join(error_messages)

    ic(final_message)
    return final_message


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

    # Optionally check if the path exists?
    # if not path.exists():
    #     logger.warning("Constructed frame path does not exist: %s", path)
    #     return None

    return path


def _get_frame_paths(video: "VideoFile") -> List[Path]:
    """Gets a list of existing frame file paths from the database Frame objects."""
    paths = []
    try:
        # Get Frame objects ordered by number
        extracted_frames = _get_frames(video).filter(extracted=True) # Use local helper
        for frame in extracted_frames:
            try:
                # Access path via image FileField
                path = Path(frame.image.path)
                if path.exists():
                    paths.append(path)
                else:
                    logger.warning("Frame object PK %s (Number: %d) points to non-existent file: %s",
                                   frame.pk, frame.frame_number, path)
                    ic(f"Warning: Frame {frame.frame_number} file missing: {path}")
            except Exception as e:
                logger.warning("Could not get path for Frame PK %s (Number: %d): %s", frame.pk, frame.frame_number, e)
                ic(f"Could not get path for frame {frame.frame_number}: {e}")
    except Exception as e:
         logger.error("Could not get frame paths for video %s: %s", video.uuid, e, exc_info=True)
         ic(f"Error getting frame paths: {e}")

    logger.debug("Retrieved %d existing frame paths for video %s.", len(paths), video.uuid)
    # Ensure paths are sorted if order matters and isn't guaranteed by the query
    # paths.sort(key=lambda p: int(p.stem.split('_')[-1])) # Example sort
    return paths


def _create_frame_object(
    video: "VideoFile", frame_number: int, image_file=None, extracted: bool = False
) -> "Frame":
    """Instantiates a Frame object (does not save it)."""
    frame_model = video.get_frame_model()
    logger.debug("Instantiating Frame object number %d for video %s.", frame_number, video.uuid)
    return frame_model(
        video_file=video,
        frame_number=frame_number,
        image=image_file, # Should be a File object or path relative to storage
        extracted=extracted,
    )


def _bulk_create_frames(video: "VideoFile", frames_to_create: List["Frame"]):
    """Bulk creates Frame objects in the database."""
    if not frames_to_create:
        logger.info("No frames provided for bulk creation for video %s.", video.uuid)
        return []

    frame_model = video.get_frame_model()
    logger.info("Bulk creating %d Frame objects for video %s.", len(frames_to_create), video.uuid)
    ic(f"Bulk creating {len(frames_to_create)} frames.")
    try:
        # Assuming ignore_conflicts=True might be useful if re-running initialization
        created_frames = frame_model.objects.bulk_create(frames_to_create, ignore_conflicts=True)
        logger.info("Successfully bulk created/ignored %d frames for video %s.", len(frames_to_create), video.uuid)
        ic(f"Bulk created/ignored {len(frames_to_create)} frames.")
        # Note: bulk_create doesn't return PKs on all backends, and doesn't call save()
        # Need to query again if PKs are needed immediately.
        return created_frames # Or query again: frame_model.objects.filter(video_file=video)
    except Exception as e:
        logger.error("Bulk creation of frames failed for video %s: %s", video.uuid, e, exc_info=True)
        ic(f"Bulk creation failed: {e}")
        raise
