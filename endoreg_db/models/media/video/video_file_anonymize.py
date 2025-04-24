import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple, Dict, Optional
import uuid
from django.db import transaction
import cv2
from tqdm import tqdm
from django.conf import settings

# import timezone for django
from django.utils import timezone


from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.validate_endo_roi import validate_endo_roi
from ....utils.video.ffmpeg_wrapper import assemble_video_from_frames

from ...utils import STORAGE_DIR, anonymize_frame # Assuming this is the base storage dir
from .video_file_segments import _get_outside_frames

if TYPE_CHECKING:
    from .video_file import VideoFile
    from ..frame import Frame
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)


def _create_anonymized_frame_files(
    video: "VideoFile",
    anonymized_frame_dir: Path,
    endo_roi: Dict[str, int],
    frames: "QuerySet[Frame]", # Expecting QuerySet from get_frames()
    outside_frame_numbers: set,
    censor_color: Tuple[int, int, int] = (0, 0, 0),
) -> List[Path]:
    """
    Creates anonymized versions of frames, censoring outside the ROI or using censor_color for 'outside' frames.

    Args:
        video: The VideoFile instance.
        anonymized_frame_dir: Directory to save anonymized frames.
        endo_roi: The endoscope region of interest dictionary.
        frames: QuerySet of all Frame objects for the video.
        outside_frame_numbers: Set of frame numbers labeled as 'outside'.
        censor_color: BGR color tuple for censoring.

    Returns:
        List of paths to the generated anonymized frame files.

    Raises:
        RuntimeError: If anonymization fails for any frame.
    """
    # from endoreg_db.models import Frame # Ensure Frame is imported if TYPE_CHECKING isn't enough

    generated_paths = []
    frame_iterator = frames.iterator()
    total_frames = frames.count() # Get count before iterating if using iterator
    progress_bar = tqdm(frame_iterator, total=total_frames, desc=f"Anonymizing frames for {video.uuid}")

    for frame_obj in progress_bar:
        try:
            frame_number = frame_obj.frame_number
            target_path = anonymized_frame_dir / f"frame_{frame_obj.frame_number:07d}.jpg"
            make_all_black = frame_obj.frame_number in outside_frame_numbers

            # --- FIX: Use frame_obj.file_path ---
            try:
                # Use the file_path property from the Frame model
                source_path = frame_obj.file_path # Get path directly from Frame object
                if not isinstance(source_path, Path):
                    raise TypeError(f"Frame.file_path did not return a Path object for frame {frame_obj.frame_number}")
            except (AttributeError, TypeError, Exception) as path_err:
                logger.error("Could not determine source path for Frame %d (PK: %s) using frame_obj.file_path: %s", frame_obj.frame_number, frame_obj.pk, path_err)
                raise RuntimeError(f"Failed to get source path for frame {frame_obj.frame_number}") from path_err
            # --- End Fix ---

            if not source_path.exists():
                logger.error("Source frame file does not exist: %s", source_path)
                # Add more context: Was the frame marked as extracted in DB?
                logger.error("Frame DB object details: PK=%s, frame_number=%d, is_extracted=%s, relative_path=%s",
                             frame_obj.pk, frame_obj.frame_number, getattr(frame_obj, 'is_extracted', 'N/A'), getattr(frame_obj, 'relative_path', 'N/A'))
                raise FileNotFoundError(f"Source frame file missing for frame {frame_obj.frame_number}: {source_path}")

            # Call the utility function directly
            anonymize_frame(
                raw_frame_path=source_path,
                target_frame_path=target_path,
                endo_roi=endo_roi,
                all_black=make_all_black,
                censor_color=censor_color
            )

            generated_paths.append(target_path)
        except (FileNotFoundError, IOError, ValueError, AttributeError, TypeError, Exception) as e: # Added TypeError
            logger.error("Error anonymizing frame %d (PK: %s): %s", frame_obj.frame_number, frame_obj.pk, e, exc_info=True)
            raise RuntimeError(f"Failed to anonymize frame {frame_obj.frame_number}") from e

    if len(generated_paths) != total_frames:
        logger.error("Mismatch in generated frames count. Expected %d, got %d.", total_frames, len(generated_paths))
        raise RuntimeError("Anonymized frame generation resulted in incorrect number of files.")

    return generated_paths


def _censor_outside_frames(video: "VideoFile", outside_label_name: str = "outside", censor_color: Tuple[int, int, int] = (0, 0, 0)) -> bool:
    """
    Overwrites frame files marked as 'outside' with a censored version (e.g., black).
    This modifies the original raw frames directly. Use with caution.
    """
    logger.warning("Starting direct censoring of 'outside' frames for video %s. This modifies raw frame files.", video.uuid)
    state = video.get_or_create_state()
    if not state.frames_extracted:
        logger.error("Frames not extracted for video %s. Cannot censor.", video.uuid)
        return False

    outside_frames = _get_outside_frames(video, outside_label_name)
    if not outside_frames:
        logger.info("No 'outside' frames found to censor for video %s.", video.uuid)
        return True

    censored_count = 0
    error_count = 0
    for frame_obj in outside_frames:
        try:
            frame_path = Path(frame_obj.relative_path)
            if not frame_path.exists():
                logger.warning("Frame file %s not found for censoring. Skipping.", frame_path)
                continue

            # Read the frame to get dimensions, then overwrite with censor color
            img = cv2.imread(str(frame_path))
            if img is None:
                logger.warning("Could not read frame %s for censoring. Skipping.", frame_path)
                continue

            img[:] = censor_color # Fill with censor color
            success = cv2.imwrite(str(frame_path), img)
            if success:
                censored_count += 1
                # Optionally update frame object state if needed
                # frame_obj.is_censored = True
                # frame_obj.save(update_fields=['is_censored'])
            else:
                logger.error("Failed to write censored frame back to %s.", frame_path)
                error_count += 1

        except Exception as e:
            logger.error("Error censoring frame %d (%s): %s",
                         frame_obj.frame_number, getattr(frame_obj, 'relative_path', 'N/A'), e, exc_info=True)
            error_count += 1

    logger.info("Finished censoring for video %s. Censored: %d, Errors: %d", video.uuid, censored_count, error_count)
    return error_count == 0


def _make_temporary_anonymized_frames(video: "VideoFile") -> Tuple[Path, List[Path]]:
    """Creates temporary anonymized frames in a separate directory."""
    if video.is_processed:
        raise ValueError("Cannot create temporary anonymized frames from a video that is already processed.")
    if not video.has_raw:
        raise ValueError("Cannot create temporary anonymized frames: Raw file is missing.")

    temp_anonym_frame_dir = video.get_temp_anonymized_frame_dir() # Use IO helper
    temp_anonym_frame_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Creating temporary anonymized frames in %s", temp_anonym_frame_dir)

    endo_roi = video.get_endo_roi() # Use Meta helper
    if not validate_endo_roi(endo_roi_dict=endo_roi):
        raise ValueError(f"Endoscope ROI is not valid for video {video.uuid}")

    state = video.get_or_create_state() # Use State helper
    if not state.frames_extracted:
        logger.info("Raw frames not extracted for %s, extracting now.", video.uuid)
        video.extract_frames(overwrite=False) # Use Frame helper

    all_frames = video.get_frames() # Use Frame helper
    if not all_frames.exists():
        raise FileNotFoundError(f"No frame objects found for video {video.uuid} after extraction attempt.")

    outside_frames = _get_outside_frames(video) # Use Segment helper
    outside_frame_numbers = {frame.frame_number for frame in outside_frames}

    logger.info("Generating %d temporary anonymized frame files...", all_frames.count())
    generated_frame_paths = _create_anonymized_frame_files(
        video=video,
        anonymized_frame_dir=temp_anonym_frame_dir,
        endo_roi=endo_roi,
        frames=all_frames,
        outside_frame_numbers=outside_frame_numbers,
    )
    logger.info("Generated %d temporary anonymized frame files.", len(generated_frame_paths))
    return temp_anonym_frame_dir, generated_frame_paths


@transaction.atomic
def _anonymize(video: "VideoFile", delete_original_raw: bool = True) -> Path:
    """Anonymizes the video by censoring frames and creating a new processed video file."""
    if video.is_processed:
        raise ValueError("Video is already processed.")
    if not video.has_raw:
        raise ValueError("Raw file is missing, cannot anonymize.")

    # Check if SensitiveMeta is validated
    if not video.sensitive_meta or not video.sensitive_meta.is_verified:
        raise ValueError(f"Sensitive metadata for video {video.uuid} is not validated. Cannot anonymize.")

    # Check if all "outside" LabelVideoSegments are validated
    outside_segments = video.get_outside_segments(only_validated=True)
    if not all(segment.is_validated for segment in outside_segments):
        raise ValueError(f"Not all 'outside' label segments for video {video.uuid} are validated. Cannot anonymize.")

    logger.info("Starting anonymization process for video %s", video.uuid)
    original_raw_file_path = video.get_raw_file_path() # Use helper
    original_raw_frame_dir = video.get_frame_dir_path() # Use IO helper

    temp_anonym_frame_dir = None
    anonymized_video_path = None
    try:
        temp_anonym_frame_dir, generated_frame_paths = _make_temporary_anonymized_frames(video)
        if not generated_frame_paths:
            raise RuntimeError("Failed to generate temporary anonymized frames.")

        anonymized_video_path = video.get_target_anonymized_video_path() # Use IO helper
        anonymized_video_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure target path doesn't exist before assembly
        anonymized_video_path.unlink(missing_ok=True)

        fps = video.get_fps() # Use Meta helper
        if fps is None:
            raise ValueError(f"FPS could not be determined for {video}, cannot assemble video.")

        logger.info("Assembling anonymized video at %s", anonymized_video_path)
        assemble_video_from_frames(
            frame_paths=generated_frame_paths,
            output_path=anonymized_video_path,
            fps=fps,
        )

        if not anonymized_video_path.exists():
            raise RuntimeError(f"Processed video file not found after assembly: {anonymized_video_path}")

        # Calculate hash of the new processed file
        new_processed_hash = get_video_hash(anonymized_video_path)
        # Check for hash collision
        if type(video).objects.filter(processed_video_hash=new_processed_hash).exclude(pk=video.pk).exists():
            raise ValueError(f"Processed video hash {new_processed_hash} already exists for another video.")

       # Update video instance fields
        video.processed_video_hash = new_processed_hash
        # Store relative path for FileField
        video.processed_file.name = video.get_target_anonymized_video_path().relative_to(settings.MEDIA_ROOT).as_posix()

        update_fields = [
            "processed_video_hash",
            "processed_file", # Use the actual FileField name
            "frame_dir",
        ]

        # Cleanup original raw assets if requested
        if delete_original_raw:
            # Get paths *before* clearing fields
            original_raw_file_path_to_delete = video.get_raw_file_path()
            original_raw_frame_dir_to_delete = video.get_frame_dir_path()

            # Clear fields on the instance
            video.raw_file.name = None # Clear FileField correctly
            # original hash should remain for reference

            # Add fields to update list
            update_fields.extend(["raw_file", "video_hash"]) # Use correct field names

            # Schedule physical file deletion after commit
            transaction.on_commit(lambda: _cleanup_raw_assets(
                video_uuid=video.uuid, # Pass UUID instead of instance
                raw_file_path=original_raw_file_path_to_delete,
                raw_frame_dir=original_raw_frame_dir_to_delete
            ))


        # Save the updated fields
        video.save(update_fields=update_fields)

        state = video.get_or_create_state()  # REMOVE the extra 'video' argument
        state.anonymized = True
        state.save(update_fields=['anonymized'])
        logger.info("Set state.anonymized to True for video %s", video.uuid)


        logger.info("Successfully anonymized video %s. Processed hash: %s", video.uuid, new_processed_hash)
        _anonymization_successful = True # Local variable, not saved
        return anonymized_video_path # Return the path on success


    except Exception as e:
        logger.error("Anonymization failed for video %s: %s", video.uuid, e, exc_info=True)
        # Clean up potentially created processed file if error occurred after creation
        if anonymized_video_path and anonymized_video_path.exists():
            logger.warning("Cleaning up potentially orphaned processed file due to error: %s", anonymized_video_path)
            anonymized_video_path.unlink(missing_ok=True)
        # Re-raise the error wrapped in a RuntimeError
        raise RuntimeError(f"Anonymization failed for {video.uuid}") from e

    finally:
        # Clean up temporary anonymized frames directory regardless of success/failure
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            logger.info("Cleaning up temporary anonymized frame directory: %s", temp_anonym_frame_dir)
            shutil.rmtree(temp_anonym_frame_dir)

def _cleanup_raw_assets(video_uuid: uuid.UUID, raw_file_path: Optional[Path]=None, raw_frame_dir: Optional[Path]=None):
    """Deletes the original raw video file and its extracted frames directory."""
    from endoreg_db.models import VideoFile
    logger.info("Performing post-commit cleanup of raw assets for video %s.", video_uuid)
    video_file = VideoFile.objects.filter(uuid=video_uuid).first()
    video_file.state.frames_extracted = False # Reset the state
    try:
        if raw_file_path and raw_file_path.exists():
            logger.info("Deleting original raw video file: %s", raw_file_path)
            raw_file_path.unlink()
        elif raw_file_path:
            logger.warning("Original raw video file %s not found for post-commit deletion.", raw_file_path)

        if raw_frame_dir and raw_frame_dir.exists():
            logger.info("Deleting original raw frame directory: %s", raw_frame_dir)
            shutil.rmtree(raw_frame_dir, ignore_errors=True)

           
        elif raw_frame_dir:
            logger.warning("Original raw frame directory %s not found for post-commit deletion.", raw_frame_dir)



        video_file.state.save(update_fields=['frames_extracted'])

    except Exception as e:
        logger.error("Error during post-commit cleanup of raw assets for video %s: %s", video_uuid, e, exc_info=True)