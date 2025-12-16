import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from django.db import OperationalError
from tqdm import tqdm

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def _initialize_frames(video: "VideoFile", frame_paths: Optional[List[Path]] = None):
    """
    Initializes Frame objects in the database based on either provided existing
    frame paths or the expected frame count from the video metadata.

    If `frame_paths` is provided, Frame objects are created/updated and marked
    as `is_extracted=True`.
    If `frame_paths` is None, Frame objects are created based on `video.frame_count`
    and marked as `is_extracted=False`.

    Updates state.frames_initialized and state.frame_count.
    Uses bulk_create with ignore_conflicts=True, so it won't fail if frames already exist.

    Raises RuntimeError on failure to create/update frames or update state.

    State Transitions:
        - On Success: Sets state.frames_initialized=True, state.frame_count=N.
        - On Failure: Does not change state (error is raised).
    """
    from endoreg_db.models import Frame
    from endoreg_db.models.media.video.video_file_frames._bulk_create_frames import _bulk_create_frames
    from endoreg_db.models.media.video.video_file_frames._create_frame_object import _create_frame_object

    frames_to_create = []
    num_expected_or_provided = 0
    mark_as_extracted = False

    if frame_paths:
        logger.info("Initializing Frame objects based on %d provided paths for video %s.", len(frame_paths), video.uuid)
        mark_as_extracted = True
        num_expected_or_provided = len(frame_paths)
        for frame_path in tqdm(frame_paths, desc=f"Initializing Frames from Paths {video.uuid}", unit="frame"):
            try:
                frame_number = int(frame_path.stem.split("_")[-1])
                relative_path_str = frame_path.name
                frames_to_create.append(_create_frame_object(video, frame_number, relative_path_str, extracted=mark_as_extracted))
            except (ValueError, IndexError) as e:
                logger.warning("Could not parse frame number from %s: %s", frame_path.name, e)
                continue
    else:
        expected_frame_count = video.frame_count
        if expected_frame_count is None or expected_frame_count <= 0:
            logger.warning("Cannot initialize frames for video %s: Frame count is %s.", video.uuid, expected_frame_count)
            try:
                state = video.get_or_create_state()
                if state.frames_initialized or state.frame_count is not None:
                    state.frames_initialized = False
                    state.frame_count = None
                    state.save(update_fields=["frames_initialized", "frame_count"])
            except Exception as state_e:
                logger.error("Failed to reset state during empty initialization for video %s: %s", video.uuid, state_e, exc_info=True)
            return

        logger.info("Initializing %d expected Frame objects for video %s (is_extracted=False).", expected_frame_count, video.uuid)
        mark_as_extracted = False
        num_expected_or_provided = expected_frame_count
        for frame_number in tqdm(range(expected_frame_count), desc=f"Initializing Expected Frames {video.uuid}", unit="frame"):
            relative_path_str = f"frame_{frame_number:07d}.jpg"
            frames_to_create.append(_create_frame_object(video, frame_number, relative_path_str, extracted=mark_as_extracted))

    if frames_to_create:
        for attempt in range(5):
            try:
                _bulk_create_frames(video, frames_to_create)
                num_created_or_ignored = len(frames_to_create)
                logger.info("Bulk create attempted for %d Frame objects for video %s (ignore_conflicts=True).", num_created_or_ignored, video.uuid)

                if mark_as_extracted:
                    frame_numbers_to_update = [f.frame_number for f in frames_to_create]
                    if frame_numbers_to_update:
                        update_count = Frame.objects.filter(video=video, frame_number__in=frame_numbers_to_update, is_extracted=False).update(is_extracted=True)
                        if update_count > 0:
                            logger.info("Marked %d existing Frame objects as is_extracted=True for video %s.", update_count, video.uuid)

                try:
                    state = video.get_or_create_state()
                    state.frames_initialized = True
                    state.frame_count = num_expected_or_provided
                    state.save(update_fields=["frames_initialized", "frame_count"])
                    logger.info("Set frames_initialized=True and frame_count=%d for video %s.", num_expected_or_provided, video.uuid)
                except Exception as state_e:
                    logger.error("Failed to update state after frame initialization for video %s: %s", video.uuid, state_e, exc_info=True)
                    raise RuntimeError(f"Failed to update state after frame initialization for video {video.uuid}") from state_e

            except OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning("Database is locked, retrying frame initialization for video %s (attempt %d/5).", video.uuid, attempt + 1)
                    time.sleep(2**attempt)
                    if attempt < 4:
                        continue
                    else:
                        raise RuntimeError(f"Failed to initialize frames for video {video.uuid}.") from e
                logger.error("Error initializing frames for video %s: %s", video.uuid, e, exc_info=True)
                raise RuntimeError(f"Failed to initialize frames for video {video.uuid}.") from e

    else:
        logger.warning("No valid frames found/generated to initialize for video %s.", video.uuid)
        try:
            state = video.get_or_create_state()
            if state.frames_initialized or state.frame_count is not None:
                state.frames_initialized = False
                state.frame_count = None
                state.save(update_fields=["frames_initialized", "frame_count"])
        except Exception as state_e:
            logger.error("Failed to reset state during empty initialization for video %s: %s", video.uuid, state_e, exc_info=True)
