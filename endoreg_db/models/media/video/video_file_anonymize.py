import logging
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

import cv2
from django.db import transaction
from tqdm import tqdm

from endoreg_db.utils.hashs import get_video_hash
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.utils.validate_endo_roi import validate_endo_roi

from ....utils.video.ffmpeg_wrapper import assemble_video_from_frames
from ...utils import anonymize_frame  # Import from models.utils
from .video_file_segments import _get_outside_frame_numbers, _get_outside_frames

if TYPE_CHECKING:
    from django.db.models import QuerySet

    from ..frame import Frame
    from .video_file import VideoFile

logger = logging.getLogger(__name__)


def _create_anonymized_frame_files(
    video: "VideoFile",
    anonymized_frame_dir: Path,
    endo_roi: Dict[str, int],
    frames: "QuerySet[Frame]",
    outside_frame_numbers: Set[int],
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
    generated_paths = []
    frame_iterator = frames.filter(is_extracted=True).iterator()
    total_frames = frames.filter(is_extracted=True).count()
    progress_bar = tqdm(frame_iterator, total=total_frames, desc=f"Anonymizing frames for {video.uuid}")

    for frame_obj in progress_bar:
        try:
            _frame_number = frame_obj.frame_number
            target_path = anonymized_frame_dir / f"frame_{frame_obj.frame_number:07d}.jpg"
            make_all_black = frame_obj.frame_number in outside_frame_numbers

            try:
                source_path = frame_obj.file_path
                if not isinstance(source_path, Path):
                    raise TypeError(f"Frame.file_path did not return a Path object for frame {frame_obj.frame_number}")
            except (AttributeError, TypeError, Exception) as path_err:
                logger.error(
                    "Could not determine source path for Frame %d (PK: %s) using frame_obj.file_path: %s", frame_obj.frame_number, frame_obj.pk, path_err
                )
                raise RuntimeError(f"Failed to get source path for frame {frame_obj.frame_number}") from path_err

            if not source_path.exists():
                error_msg = (
                    f"CRITICAL INCONSISTENCY: Source frame file missing for frame {frame_obj.frame_number} "
                    f"(PK: {frame_obj.pk}, Path: {source_path}) despite is_extracted=True for video {video.uuid}. "
                    f"Halting anonymization."
                )
                logger.error(error_msg)
                raise FileNotFoundError(error_msg)

            anonymize_frame(raw_frame_path=source_path, target_frame_path=target_path, endo_roi=endo_roi, all_black=make_all_black, censor_color=censor_color)

            generated_paths.append(target_path)
        except (FileNotFoundError, IOError, ValueError, AttributeError, TypeError, Exception) as e:
            logger.error("Error anonymizing frame %d (PK: %s): %s", frame_obj.frame_number, frame_obj.pk, e, exc_info=True)
            raise RuntimeError(f"Failed to anonymize frame {frame_obj.frame_number}") from e

    if len(generated_paths) != total_frames:
        logger.error("Mismatch in generated frames count. Expected %d, got %d.", total_frames, len(generated_paths))
        raise RuntimeError("Anonymized frame generation resulted in incorrect number of files.")

    return generated_paths


def _censor_outside_frames(video: "VideoFile", outside_label_name: str = "outside", censor_color: Tuple[int, int, int] = (0, 0, 0)) -> bool:
    """
    Overwrites frame files marked as 'outside' with a censored version (e.g., black).
    This modifies the original raw frames directly. Use with caution. Requires frames to be extracted.
    Raises ValueError if pre-condition not met. Returns True on success, False if any frame fails.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True.
        - Post-condition: No state changes.
    """
    logger.warning("Starting direct censoring of 'outside' frames for video %s. This modifies raw frame files.", video.uuid)
    state = video.get_or_create_state()
    if not state.frames_extracted:
        raise ValueError(f"Frames not extracted for video {video.uuid}. Cannot censor.")

    outside_frames = _get_outside_frames(video, outside_label_name)
    if not outside_frames:
        logger.info("No 'outside' frames found to censor for video %s.", video.uuid)
        return True

    censored_count = 0
    error_count = 0
    for frame_obj in outside_frames:
        try:
            frame_path = frame_obj.file_path
            if not frame_path:
                logger.warning("Could not get file path for frame %d. Skipping censoring.", frame_obj.frame_number)
                error_count += 1
                continue

            if not frame_path.exists():
                logger.warning("Frame file %s not found for censoring. Skipping.", frame_path)
                continue

            img = cv2.imread(str(frame_path))
            if img is None:
                logger.warning("Could not read frame %s for censoring. Skipping.", frame_path)
                continue

            img[:] = censor_color
            success = cv2.imwrite(str(frame_path), img)
            if success:
                censored_count += 1
            else:
                logger.error("Failed to write censored frame back to %s.", frame_path)
                error_count += 1

        except Exception as e:
            logger.error("Error censoring frame %d (%s): %s", frame_obj.frame_number, getattr(frame_obj, "relative_path", "N/A"), e, exc_info=True)
            error_count += 1

    logger.info("Finished censoring for video %s. Censored: %d, Errors: %d", video.uuid, censored_count, error_count)
    return error_count == 0


def _make_temporary_anonymized_frames(video: "VideoFile", roi_processing = True) -> Tuple[Path, List[Path]]:
    """
    Creates temporary anonymized frames in a separate directory.
    Requires raw file and extracted frames. Raises ValueError or RuntimeError on failure.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True (or triggers extraction).
        - Post-condition: No state changes directly by this function.
    """
    if video.is_processed:
        raise ValueError(f"Cannot create temporary anonymized frames for video {video.uuid}: already processed.")
    if not video.has_raw:
        raise ValueError(f"Cannot create temporary anonymized frames for video {video.uuid}: Raw file is missing.")

    temp_anonym_frame_dir = video.get_temp_anonymized_frame_dir()
    temp_anonym_frame_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Creating temporary anonymized frames for video %s in %s", video.uuid, temp_anonym_frame_dir)
    if roi_processing:
        endo_roi = video.get_endo_roi()
        if not validate_endo_roi(endo_roi_dict=endo_roi):
            raise ValueError(f"Endoscope ROI is not valid for video {video.uuid}")
    else:
        endo_roi = {"x": 0, "y": 0, "width": 0, "height": 0}  # Dummy ROI to skip processing
    assert endo_roi is not None  # For type checker
    
        

    state = video.get_or_create_state()
    if not state.frames_extracted:
        logger.info("Raw frames not extracted for %s, extracting now.", video.uuid)
        try:
            if not video.extract_frames(overwrite=False):
                raise RuntimeError(f"Frame extraction method returned False unexpectedly for video {video.uuid}.")
            state.refresh_from_db()
            if not state.frames_extracted:
                raise RuntimeError(f"Frame extraction did not update state for video {video.uuid}, cannot create anonymized frames.")
        except Exception as extract_e:
            logger.error("Frame extraction failed during anonymization prep for video %s: %s", video.uuid, extract_e, exc_info=True)
            raise RuntimeError(f"Frame extraction failed for video {video.uuid}, cannot create anonymized frames.") from extract_e

    all_frames = video.get_frames()
    if not all_frames.exists():
        raise FileNotFoundError(f"No frame objects found for video {video.uuid} after extraction attempt.")

    outside_frame_numbers = _get_outside_frame_numbers(video)

    logger.info("Generating %d temporary anonymized frame files for video %s...", all_frames.filter(is_extracted=True).count(), video.uuid)
    generated_frame_paths = _create_anonymized_frame_files(
        video=video,
        anonymized_frame_dir=temp_anonym_frame_dir,
        endo_roi=endo_roi,
        frames=all_frames,
        outside_frame_numbers=outside_frame_numbers,
    )
    logger.info("Generated %d temporary anonymized frame files for video %s.", len(generated_frame_paths), video.uuid)
    return temp_anonym_frame_dir, generated_frame_paths


@transaction.atomic
def _anonymize(video: "VideoFile", delete_original_raw: bool = True) -> bool:
    """
    Performs full anonymization of a video by censoring frames, assembling a processed video file, updating database records, and optionally deleting original raw assets.

    Raises:
        ValueError: If required preconditions are not met (e.g., frames not extracted, sensitive metadata not validated).
        FileNotFoundError: If the raw video file is missing.
        RuntimeError: If anonymization or video assembly fails.

    Returns:
        bool: True if anonymization completes successfully.
    """
    state = video.get_or_create_state()

    if state.anonymized:
        logger.info("Video %s is already marked as anonymized in state. Skipping.", video.uuid)
        return True
    if not video.has_raw:
        raise FileNotFoundError(f"Raw file is missing for video {video.uuid}, cannot anonymize.")
    if not state.frames_extracted:
        raise ValueError(f"Frames not extracted for video {video.uuid}, cannot anonymize.")
    if not video.sensitive_meta or not video.sensitive_meta.is_verified:
        raise ValueError(f"Sensitive metadata for video {video.uuid} is not validated. Cannot anonymize.")
    # outside_segments = video.get_outside_segments(only_validated=False)
    # unvalidated_outside = outside_segments.filter(state__is_validated=False)

    logger.info("Starting anonymization process for video %s", video.uuid)

    temp_anonym_frame_dir = None
    anonymized_video_path = None
    try:
        temp_anonym_frame_dir, generated_frame_paths = _make_temporary_anonymized_frames(video)
        if not generated_frame_paths:
            raise RuntimeError(f"Failed to generate temporary anonymized frames for video {video.uuid}.")

        anonymized_video_path = video.get_target_anonymized_video_path()
        anonymized_video_path.parent.mkdir(parents=True, exist_ok=True)

        anonymized_video_path.unlink(missing_ok=True)

        fps = video.get_fps()
        if fps is None:
            raise ValueError(f"FPS could not be determined for {video.uuid}, cannot assemble video.")

        logger.info("Assembling anonymized video for %s at %s", video.uuid, anonymized_video_path)
        assemble_video_from_frames(
            frame_paths=generated_frame_paths,
            output_path=anonymized_video_path,
            fps=fps,
        )

        if not anonymized_video_path.exists():
            raise RuntimeError(f"Processed video file not found after assembly for {video.uuid}: {anonymized_video_path}")

        new_processed_hash = get_video_hash(anonymized_video_path)
        if type(video).objects.filter(processed_video_hash=new_processed_hash).exclude(pk=video.pk).exists():
            raise ValueError(f"Processed video hash {new_processed_hash} already exists for another video (Video: {video.uuid}).")

        video.processed_video_hash = new_processed_hash
        video.processed_file.name = video.get_target_anonymized_video_path().relative_to(STORAGE_DIR).as_posix()

        update_fields = [
            "processed_video_hash",
            "processed_file",
            "frame_dir",
        ]

        if delete_original_raw:
            original_raw_file_path_to_delete = video.get_raw_file_path()
            original_raw_frame_dir_to_delete = video.get_frame_dir_path()

            video.raw_file.name = ""

            update_fields.extend(["raw_file", "video_hash"])

            transaction.on_commit(
                lambda: _cleanup_raw_assets(
                    video_uuid=video.uuid, raw_file_path=original_raw_file_path_to_delete, raw_frame_dir=original_raw_frame_dir_to_delete
                )
            )

        video.save(update_fields=update_fields)
        assert video.state is not None  # For type checker
        video.state.mark_anonymized(save=True)
        video.refresh_from_db()
        return True

    except Exception as e:
        logger.error("Anonymization failed for video %s: %s", video.uuid, e, exc_info=True)
        if anonymized_video_path and anonymized_video_path.exists():
            logger.warning("Cleaning up potentially orphaned processed file for video %s due to error: %s", video.uuid, anonymized_video_path)
            anonymized_video_path.unlink(missing_ok=True)
        raise RuntimeError(f"Anonymization failed for video {video.uuid}") from e

    finally:
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            logger.info("Cleaning up temporary anonymized frame directory for video %s: %s", video.uuid, temp_anonym_frame_dir)
            shutil.rmtree(temp_anonym_frame_dir)


def _cleanup_raw_assets(video_uuid: uuid.UUID, raw_file_path: Optional[Path] = None, raw_frame_dir: Optional[Path] = None):
    """
    Deletes the original raw video file and its extracted frames directory.
    Called via transaction.on_commit after successful anonymization.

    State Transitions:
        - Sets state.frames_extracted=False.
    """
    from endoreg_db.models import VideoFile, VideoState

    logger.info("Performing post-commit cleanup of raw assets for video %s.", video_uuid)
    try:
        video_file = VideoFile.objects.select_related("state").filter(uuid=video_uuid).first()
        if not video_file:
            logger.error("VideoFile %s not found during post-commit cleanup.", video_uuid)
            return
        if not video_file.state:
            logger.error("VideoState not found for VideoFile %s during post-commit cleanup.", video_uuid)
            video_file.state = VideoState.objects.create(video_file=video_file)

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

        if video_file.state.frames_extracted:
            video_file.state.frames_extracted = False
            video_file.state.save(update_fields=["frames_extracted"])
            logger.info("Set state.frames_extracted=False for video %s after raw asset cleanup.", video_uuid)

    except Exception as e:
        logger.error("Error during post-commit cleanup of raw assets for video %s: %s", video_uuid, e, exc_info=True)
