import logging
from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path
from endoreg_db.utils.video.ffmpeg_wrapper import extract_frames as ffmpeg_extract_frames
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

from django.db import transaction


import shutil
logger = logging.getLogger(__name__)

@transaction.atomic
def _extract_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False,
) -> bool:
    """
    Extracts frames from the raw video file using ffmpeg, updates Frame objects'
    is_extracted flag, and updates relevant VideoState fields atomically.
    Assumes Frame objects may already exist (created by _initialize_frames).

    Returns:
        bool: True if extraction and update were successful.
    Raises:
        FileNotFoundError: If the raw video file is missing.
        RuntimeError: If frame extraction or update fails for other reasons.
        ValueError: If frame directory cannot be determined.
    """
    from ._delete_frames import _delete_frames
    from endoreg_db.models.media.frame import Frame
    if not video.has_raw:
        raise FileNotFoundError(f"Raw video file not available for {video.uuid}. Cannot extract frames.")

    raw_file_path = video.get_raw_file_path()
    if not raw_file_path or not raw_file_path.exists():
        raise FileNotFoundError(f"Raw video file not found at {raw_file_path} for video {video.uuid}. Cannot extract frames.")

    frame_dir = _get_frame_dir_path(video)
    if not frame_dir:
        raise ValueError(f"Cannot determine frame directory path for video {video.uuid}.")

    state = video.get_or_create_state()

    frames_exist_in_db = Frame.objects.filter(video=video).exists()
    files_exist_on_disk = frame_dir.exists() and any(frame_dir.iterdir())

    if (state.frames_extracted or files_exist_on_disk) and not overwrite:
        logger.info("Frames already extracted or files exist for video %s, and overwrite=False. Skipping extraction.", video.uuid)
        if state.frames_extracted and frames_exist_in_db:
            updated_count = Frame.objects.filter(video=video, is_extracted=False).update(is_extracted=True)
            if updated_count > 0:
                logger.info("Marked %d existing Frame objects as extracted for video %s based on state.", updated_count, video.uuid)
        elif not state.frames_extracted and files_exist_on_disk:
            logger.warning("Files exist on disk for video %s but state.frames_extracted is False. State might be inconsistent.", video.uuid)
        return True

    if overwrite:
        logger.info("Overwrite=True. Preparing to delete existing frames/files for video %s before extraction.", video.uuid)
        try:
            _delete_frames(video)
            updated_count = Frame.objects.filter(video=video, is_extracted=True).update(is_extracted=False)
            if updated_count > 0:
                logger.info("Reset %d Frame objects to is_extracted=False for video %s due to overwrite.", updated_count, video.uuid)
            state.refresh_from_db()
        except Exception as del_e:
            logger.error("Failed to delete existing frames for video %s during overwrite: %s", video.uuid, del_e, exc_info=True)
            raise RuntimeError(f"Failed to delete existing frames for video {video.uuid} during overwrite.") from del_e

    frame_dir.mkdir(parents=True, exist_ok=True)

    try:
        logger.info("Starting frame extraction for video %s to %s", video.uuid, frame_dir)
        extracted_paths = ffmpeg_extract_frames(raw_file_path, frame_dir, quality=quality, ext=ext)
        if not extracted_paths:
            logger.warning("ffmpeg_extract_frames returned no paths for video %s. Check video duration and ffmpeg logs.", video.uuid)
            if video.frame_count is not None and video.frame_count > 0:
                raise RuntimeError(f"ffmpeg_extract_frames returned no paths for video {video.uuid}, but {video.frame_count} frames were expected.")
            else:
                pass

        logger.info("Successfully extracted %d frames using ffmpeg for video %s.", len(extracted_paths), video.uuid)

        extracted_frame_numbers = []
        for frame_path in extracted_paths:
            try:
                frame_number = int(frame_path.stem.split('_')[-1])
                extracted_frame_numbers.append(frame_number)
            except (ValueError, IndexError) as e:
                logger.warning("Could not parse frame number from extracted file %s: %s", frame_path.name, e)

        if extracted_frame_numbers:
            try:
                update_count = Frame.objects.filter(
                    video=video,
                    frame_number__in=extracted_frame_numbers
                ).update(is_extracted=True)
                logger.info("Marked %d Frame objects as is_extracted=True for video %s.", update_count, video.uuid)
                if update_count != len(extracted_frame_numbers):
                    logger.warning("Number of updated frames (%d) does not match number of parsed extracted files (%d) for video %s.", update_count, len(extracted_frame_numbers), video.uuid)
            except Exception as update_e:
                logger.error("Failed to update is_extracted flag for frames of video %s: %s", video.uuid, update_e, exc_info=True)

        if extracted_paths:
            state.refresh_from_db()
            if not state.frames_extracted:
                state.frames_extracted = True
                state.save(update_fields=['frames_extracted'])
                logger.info("Set frames_extracted state to True for video %s.", video.uuid)
        return True

    except Exception as e:
        logger.error("Frame extraction or update failed for video %s: %s", video.uuid, e, exc_info=True)
        logger.warning("Cleaning up frame directory %s for video %s due to extraction error.", frame_dir, video.uuid)
        shutil.rmtree(frame_dir, ignore_errors=True)
        try:
            Frame.objects.filter(video=video, is_extracted=True).update(is_extracted=False)
            state.refresh_from_db()
            if state.frames_extracted:
                state.frames_extracted = False
                state.save(update_fields=['frames_extracted'])
        except Exception as db_err:
            logger.error("Failed to reset flags/state in DB during error handling for video %s: %s", video.uuid, db_err)
        raise RuntimeError(f"Frame extraction or update failed for video {video.uuid}.") from e
