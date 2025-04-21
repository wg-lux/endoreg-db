import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, List, Tuple, Dict, Optional
from django.db import transaction
from django.core.files.base import ContentFile
import cv2
from icecream import ic

from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.validate_endo_roi import validate_endo_roi
from ....utils.video import assemble_video_from_frames # Assuming this exists or create it
from ...utils import STORAGE_DIR # Assuming this is the base storage dir

if TYPE_CHECKING:
    from .video_file import VideoFile
    from ..frame import Frame
    from django.db.models import QuerySet

logger = logging.getLogger(__name__)


def _create_anonymized_frame_files(
    video: "VideoFile",
    anonymized_frame_dir: Path,
    endo_roi: Dict[str, int],
    frames: "QuerySet[Frame]",
    outside_frame_numbers: set,
    censor_color: Tuple[int, int, int] = (0, 0, 0), # Black
) -> List[Path]:
    """
    Creates anonymized versions of frames, censoring outside the ROI or blacking out 'outside' frames.

    Args:
        video: The VideoFile instance.
        anonymized_frame_dir: Directory to save anonymized frames.
        endo_roi: The endoscope region of interest dictionary.
        frames: QuerySet of all Frame objects for the video.
        outside_frame_numbers: Set of frame numbers labeled as 'outside'.
        censor_color: BGR color tuple for censoring.

    Returns:
        List of paths to the generated anonymized frame files.
    """
    generated_paths = []
    total_frames = frames.count()
    processed_count = 0
    log_interval = max(1, total_frames // 10) # Log progress every 10%

    x, y, w, h = endo_roi['x'], endo_roi['y'], endo_roi['width'], endo_roi['height']
    img_height, img_width = video.height, video.width # Get dimensions from video

    if not img_height or not img_width:
        logger.error("Video dimensions (height/width) not available for %s. Cannot create anonymized frames.", video.uuid)
        raise ValueError(f"Video dimensions missing for {video.uuid}")

    for frame_obj in frames.iterator(): # Use iterator for memory efficiency
        processed_count += 1
        if processed_count % log_interval == 0:
            logger.debug("Anonymizing frame %d/%d for video %s", processed_count, total_frames, video.uuid)

        try:
            original_path = Path(frame_obj.image.path)
            if not original_path.exists():
                logger.warning("Original frame file missing: %s. Skipping.", original_path)
                continue

            target_path = anonymized_frame_dir / original_path.name

            # Check if frame is marked as 'outside'
            if frame_obj.frame_number in outside_frame_numbers:
                # Create a completely black frame
                black_frame = cv2.imread(str(original_path)) # Read to get dimensions if needed
                if black_frame is None:
                     logger.warning("Could not read frame %s to create black frame. Skipping.", original_path)
                     continue
                black_frame[:] = censor_color # Fill with censor color
                cv2.imwrite(str(target_path), black_frame)
            else:
                # Censor outside the ROI
                frame_img = cv2.imread(str(original_path))
                if frame_img is None:
                     logger.warning("Could not read frame %s for censoring. Skipping.", original_path)
                     continue

                # Create masks for areas to censor
                mask = cv2.rectangle(frame_img.copy(), (x, y), (x + w, y + h), (255, 255, 255), -1)
                mask_inv = cv2.bitwise_not(mask)

                # Apply censoring
                censored_img = cv2.bitwise_and(frame_img, mask) # Keep ROI
                blackout_area = cv2.bitwise_and(frame_img, mask_inv) # Get outside area
                blackout_area[:] = censor_color # Make outside area black
                final_img = cv2.add(censored_img, blackout_area) # Combine

                cv2.imwrite(str(target_path), final_img)

            generated_paths.append(target_path)

        except Exception as e:
            logger.error("Error processing frame %d (%s) for anonymization: %s",
                         frame_obj.frame_number, getattr(frame_obj.image, 'path', 'N/A'), e, exc_info=True)
            # Decide whether to continue or raise based on severity

    logger.info("Finished generating %d anonymized frames for video %s.", len(generated_paths), video.uuid)
    return generated_paths


def _assemble_anonymized_video(
    generated_frame_paths: List[Path],
    anonymized_video_path: Path,
    fps: float,
):
    """Assembles a video from a list of frame paths."""
    if not generated_frame_paths:
        raise ValueError("No frame paths provided to assemble video.")

    logger.info("Assembling video from %d frames to %s at %.2f FPS.",
                len(generated_frame_paths), anonymized_video_path, fps)

    # Ensure paths are sorted correctly by frame number if not already guaranteed
    # Example sorting key based on 'frame_0000001.jpg' format
    try:
        sorted_paths = sorted(generated_frame_paths, key=lambda p: int(p.stem.split('_')[-1]))
    except (IndexError, ValueError):
        logger.warning("Could not sort frame paths numerically, using provided order.")
        sorted_paths = generated_frame_paths

    # Use a utility function (assumed to exist or needs to be created)
    # This function handles OpenCV VideoWriter setup and frame writing loop
    assemble_video_from_frames(
        frame_paths=[p.as_posix() for p in sorted_paths],
        output_path=anonymized_video_path.as_posix(),
        fps=fps
    )
    logger.info("Video assembly completed: %s", anonymized_video_path)


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
            frame_path = Path(frame_obj.image.path)
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
                         frame_obj.frame_number, getattr(frame_obj.image, 'path', 'N/A'), e, exc_info=True)
            error_count += 1

    logger.info("Finished censoring for video %s. Censored: %d, Errors: %d", video.uuid, censored_count, error_count)
    return error_count == 0


def _make_temporary_anonymized_frames(video: "VideoFile") -> Tuple[Path, List[Path]]:
    """Creates temporary anonymized frames in a separate directory."""
    if video.is_processed:
        raise ValueError("Cannot create temporary anonymized frames from a video that is already processed.")
    if not video.has_raw:
        raise ValueError("Cannot create temporary anonymized frames: Raw file is missing.")

    temp_anonym_frame_dir = video._get_temp_anonymized_frame_dir() # Use IO helper
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

    logger.info("Starting anonymization process for video %s", video.uuid)
    original_raw_file_path = video.get_raw_file_path() # Use helper
    original_raw_frame_dir = video.get_frame_dir_path() # Use IO helper

    temp_anonym_frame_dir = None
    anonymized_video_path = None
    try:
        temp_anonym_frame_dir, generated_frame_paths = _make_temporary_anonymized_frames(video)
        if not generated_frame_paths:
             raise RuntimeError("Failed to generate temporary anonymized frames.")

        anonymized_video_path = video._get_target_anonymized_video_path() # Use IO helper
        anonymized_video_path.parent.mkdir(parents=True, exist_ok=True)

        # Ensure target path doesn't exist before assembly
        anonymized_video_path.unlink(missing_ok=True)

        fps = video.get_fps() # Use Meta helper
        if fps is None:
            raise ValueError(f"FPS could not be determined for {video}, cannot assemble video.")

        logger.info("Assembling anonymized video at %s", anonymized_video_path)
        _assemble_anonymized_video(
            generated_frame_paths=generated_frame_paths,
            anonymized_video_path=anonymized_video_path,
            fps=fps,
        )

        if not anonymized_video_path.exists():
             raise RuntimeError(f"Processed video file not found after assembly: {anonymized_video_path}")

        # Calculate hash of the new processed file
        new_processed_hash = get_video_hash(anonymized_video_path)
        # Check if this hash already exists for *another* video's processed file
        if type(video).objects.filter(processed_video_hash=new_processed_hash).exclude(pk=video.pk).exists():
             # Clean up the newly created file before raising error
             anonymized_video_path.unlink(missing_ok=True)
             raise ValueError(f"Another VideoFile already exists with processed hash {new_processed_hash}")

        logger.info("Updating VideoFile instance %s with processed file info.", video.uuid)
        # Store path relative to STORAGE_DIR
        relative_path = anonymized_video_path.relative_to(STORAGE_DIR).as_posix()
        video.processed_file.name = relative_path
        video.processed_video_hash = new_processed_hash

        state = video.get_or_create_state() # Use State helper
        state.anonymized = True
        state.frames_extracted = False # Raw frames are no longer relevant/available if deleted

        update_fields = ['processed_file', 'processed_video_hash', 'state']

        if delete_original_raw:
            logger.info("Flagging raw file and frames for deletion for video %s.", video.uuid)
            # Delete the file field content, but don't save the model yet
            video.raw_file.delete(save=False)
            # Mark the field itself as null in the update list
            video.raw_file = None # Important to set field to None
            update_fields.append('raw_file')
            # Also clear frame_dir if raw frames are being deleted
            video.frame_dir = None
            update_fields.append('frame_dir')


        # Save the VideoFile instance with updated fields
        video.save(update_fields=update_fields)
        # Save the state changes
        state.save() # State is saved separately

        logger.info("Successfully processed video %s. New processed path: %s", video.uuid, anonymized_video_path)

        # Schedule cleanup of original assets after transaction commits
        if delete_original_raw:
             transaction.on_commit(lambda: _cleanup_raw_assets(video, original_raw_file_path, original_raw_frame_dir))

        return anonymized_video_path

    except Exception as e:
        logger.error("Anonymization failed for video %s: %s", video.uuid, e, exc_info=True)
        # Clean up the potentially created processed video file if an error occurred
        if anonymized_video_path and anonymized_video_path.exists():
            logger.warning("Cleaning up potentially orphaned processed file due to error: %s", anonymized_video_path)
            anonymized_video_path.unlink(missing_ok=True)
        # Re-raise the exception to ensure transaction rollback
        raise RuntimeError(f"Anonymization failed for {video.uuid}") from e
    finally:
        # Always clean up the temporary frame directory
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            logger.debug("Cleaning up temporary anonymized frame directory: %s", temp_anonym_frame_dir)
            shutil.rmtree(temp_anonym_frame_dir, ignore_errors=True)


def _cleanup_raw_assets(video: "VideoFile", raw_file_path: Optional[Path], raw_frame_dir: Optional[Path]):
    """Deletes the original raw video file and its extracted frames directory."""
    logger.info("Performing post-commit cleanup of raw assets for video %s.", video.uuid)
    try:
        if raw_file_path and raw_file_path.exists():
            logger.info("Deleting original raw video file: %s", raw_file_path)
            raw_file_path.unlink()
        elif raw_file_path:
             logger.warning("Original raw video file %s not found for post-commit deletion.", raw_file_path)

        if raw_frame_dir and raw_frame_dir.exists():
             logger.info("Deleting original raw frame directory: %s", raw_frame_dir)
             shutil.rmtree(raw_frame_dir, ignore_errors=True)
             # Also delete Frame objects from DB if they weren't deleted earlier
             try:
                 count, _ = video.frames.all().delete()
                 if count > 0:
                     logger.info("Deleted %d residual Frame DB objects during raw asset cleanup.", count)
             except Exception as db_del_e:
                 logger.error("Error deleting residual Frame DB objects for %s: %s", video.uuid, db_del_e)

        elif raw_frame_dir:
             logger.warning("Original raw frame directory %s not found for post-commit deletion.", raw_frame_dir)

    except Exception as e:
        logger.error("Error during post-commit cleanup of raw assets for video %s: %s", video.uuid, e, exc_info=True)

