# endoreg_db/services/video_processing/video_cleanup_on_error.py
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, MutableSet, Optional

logger = logging.getLogger(__name__)


def cleanup_video_on_error(
    *,
    current_video: Any,
    original_file_path: Optional[str | Path],
    processing_context: Dict[str, Any],
) -> None:
    """
    Cleanup processing context on error for video imports.

    This is extracted from VideoImportService._cleanup_on_error and kept as
    close as possible to the original behavior.
    """
    try:
        if not current_video or not hasattr(current_video, "state"):
            # Nothing we can sensibly do here
            return

        # Ensure state exists
        if current_video.state is None:
            try:
                current_video.get_or_create_state()
            except Exception as e:
                logger.warning(
                    "Video state not found for video %s during error cleanup: %s",
                    getattr(current_video, "uuid", None),
                    e,
                )
                return

        current_video.state = current_video.get_or_create_state()

        # Try to restore original raw file
        try:
            if original_file_path is not None:
                original_path = Path(original_file_path)
                if not original_path.exists():
                    raise AssertionError("Original file path does not exist")

                logger.info("Marked video import as failed in state")
                raw_file_path = getattr(getattr(current_video, "raw_file", None), "path", None)
                if raw_file_path and original_file_path:
                    shutil.copy2(str(raw_file_path), str(original_file_path))
                else:
                    logger.warning("Cannot restore original raw file: path is None")
            else:
                logger.warning("Original file path is None")
        except AssertionError:
            logger.warning("Original file path does not exist")

        # Reset state flags if processing had started
        try:
            from endoreg_db.models.state import VideoState  # local import to avoid cycles

            if not isinstance(current_video.state, VideoState):
                logger.error("Current video state is not a VideoState instance during cleanup")
                raise AssertionError

            if processing_context.get("processing_started"):
                current_video.state.frames_extracted = False
                current_video.state.frames_initialized = False
                current_video.state.video_meta_extracted = False
                current_video.state.text_meta_extracted = False
                current_video.state.save()
        except Exception as e:
            logger.warning("Error during video error cleanup: %s", e)
    except Exception as outer_exc:
        logger.warning("Unexpected error in cleanup_video_on_error: %s", outer_exc)


def cleanup_video_processing_context(
    *,
    processing_context: Dict[str, Any],
    processed_files: MutableSet[str],
) -> None:
    """
    Cleanup processing context and release file lock for video imports.

    Extracted from VideoImportService._cleanup_processing_context.
    """
    # DEFENSIVE: ensure dict
    if processing_context is None:
        processing_context = {}

    # Release file lock if it was acquired
    try:
        lock_context = processing_context.get("_lock_context")
        if lock_context is not None:
            try:
                lock_context.__exit__(None, None, None)
                logger.info("Released file lock")
            except Exception as e:
                logger.warning("Error releasing file lock during context cleanup: %s", e)
    except Exception as e:
        logger.warning("Error while handling lock release in context cleanup: %s", e)

    # Remove file from processed_files set if processing failed
    try:
        file_path = processing_context.get("file_path")
        anonymization_completed = processing_context.get("anonymization_completed")

        if file_path and not anonymization_completed:
            file_path_str = str(file_path)
            if file_path_str in processed_files:
                processed_files.remove(file_path_str)
                logger.info(
                    "Removed %s from processed files (failed processing)",
                    file_path_str,
                )
    except Exception as e:
        logger.warning("Error while cleaning processed_files set: %s", e)
