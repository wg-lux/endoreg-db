import logging
import os
from typing import TYPE_CHECKING

from django.db import transaction

from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path

# Assuming ffmpeg_wrapper has or will have this function
from endoreg_db.utils.video.ffmpeg_wrapper import extract_frame_range as ffmpeg_extract_frame_range

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def _delete_frame_range(video: "VideoFile", start_frame: int, end_frame: int):
    """
    Deletes frame image files within the specified range [start_frame, end_frame)
    and updates their is_extracted status to False. Runs within the caller's transaction.
    """

    logger.info("Deleting frame files for video %s in range [%d, %d)", video.uuid, start_frame, end_frame)
    frames_to_delete = video.frames.filter(frame_number__gte=start_frame, frame_number__lt=end_frame, is_extracted=True)

    deleted_count = 0
    paths_to_delete = [frame.file_path for frame in frames_to_delete]  # Get paths before potential DB changes

    # Update DB first
    update_count = frames_to_delete.update(is_extracted=False)
    logger.info("Marked %d Frame objects as is_extracted=False for video %s range [%d, %d).", update_count, video.uuid, start_frame, end_frame)

    # Then delete files
    for frame_path in paths_to_delete:
        try:
            if frame_path.exists():
                os.remove(frame_path)
                deleted_count += 1
        except Exception as e:
            # Log warning but continue; DB state is already updated.
            logger.warning("Could not delete frame file %s for video %s: %s", frame_path, video.uuid, e)

    logger.info(
        "Attempted deletion of %d files for video %s range [%d, %d). Actual deleted: %d.",
        len(paths_to_delete),
        video.uuid,
        start_frame,
        end_frame,
        deleted_count,
    )


@transaction.atomic
def _extract_frame_range(
    video: "VideoFile",
    start_frame: int,
    end_frame: int,
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False,
) -> bool:
    """
    Extracts frames within the range [start_frame, end_frame) using ffmpeg.
    Updates corresponding Frame objects' is_extracted flag.
    Runs within the caller's transaction.
    Does NOT update VideoState.frames_extracted.
    """

    if not video.has_raw:
        raise FileNotFoundError(f"Raw video file not available for {video.uuid}. Cannot extract frame range.")

    raw_file_path = video.get_raw_file_path()
    if not raw_file_path or not raw_file_path.exists():
        raise FileNotFoundError(f"Raw video file not found at {raw_file_path} for video {video.uuid}. Cannot extract frame range.")

    frame_dir = _get_frame_dir_path(video)
    if not frame_dir:
        raise ValueError(f"Cannot determine frame directory path for video {video.uuid}.")

    # Check frames within the range that might already exist
    frames_in_range = video.frames.filter(frame_number__gte=start_frame, frame_number__lt=end_frame)
    existing_extracted_in_range = frames_in_range.filter(is_extracted=True)

    if existing_extracted_in_range.exists():
        if overwrite:
            logger.info("Overwrite=True, deleting existing frame files in range [%d, %d) for video %s before extraction.", start_frame, end_frame, video.uuid)
            _delete_frame_range(video, start_frame, end_frame)
        else:
            logger.info(
                "Frames already exist in range [%d, %d) for video %s and overwrite=False. Skipping extraction for this range.",
                start_frame,
                end_frame,
                video.uuid,
            )
            updated_count = frames_in_range.filter(is_extracted=False).update(is_extracted=True)
            if updated_count > 0:
                logger.info("Marked %d existing Frame objects in range [%d, %d) as extracted for video %s.", updated_count, start_frame, end_frame, video.uuid)
            return True  # Indicate success as frames are considered present

    frame_dir.mkdir(parents=True, exist_ok=True)
    extracted_paths = []

    try:
        logger.info("Starting frame range extraction [%d, %d) for video %s to %s", start_frame, end_frame, video.uuid, frame_dir)
        extracted_paths = ffmpeg_extract_frame_range(raw_file_path, frame_dir, start_frame, end_frame, quality=quality, ext=ext)

        logger.info(
            "ffmpeg extraction process completed for video %s range [%d, %d). Found %d files.", video.uuid, start_frame, end_frame, len(extracted_paths)
        )

        update_count = frames_in_range.update(is_extracted=True)
        logger.info("Marked %d Frame objects in range [%d, %d) as is_extracted=True for video %s.", update_count, start_frame, end_frame, video.uuid)

        return True

    except FileNotFoundError as err:
        logger.error(
            "Frame range extraction [%d, %d) failed for video %s: %s",
            start_frame,
            end_frame,
            video.uuid,
            err,
            exc_info=True,
        )
        raise

    except Exception as e:
        logger.error("Frame range extraction [%d, %d) or DB update failed for video %s: %s", start_frame, end_frame, video.uuid, e, exc_info=True)

        logger.warning("Attempting file cleanup in range [%d, %d) for video %s due to extraction error.", start_frame, end_frame, video.uuid)
        files_to_check = extracted_paths if "extracted_paths" in locals() and extracted_paths else []
        if not files_to_check:
            files_to_check = [frame_dir / f"frame_{i:07d}.{ext}" for i in range(start_frame, end_frame)]

        for potential_file in files_to_check:
            if potential_file.exists():
                try:
                    os.remove(potential_file)
                except OSError as unlink_err:
                    logger.error("Failed to delete potential frame %s during cleanup: %s", potential_file, unlink_err)

        raise RuntimeError(f"Frame range extraction or update failed for video {video.uuid} range [{start_frame}, {end_frame}).") from e
