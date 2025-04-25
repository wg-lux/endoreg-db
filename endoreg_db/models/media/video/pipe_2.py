import logging
from typing import TYPE_CHECKING
from django.db import transaction

# --- Import necessary models for type hints and operations ---
from ...state import VideoState
# --- End Imports ---

# Configure logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState


# --- Helper Step Functions for Pipe 2 ---

def _pipe_2_step_ensure_state(video_file: "VideoFile") -> "VideoState":
    """Ensures the VideoState object exists."""
    logger.debug("Pipe 2 Step: Ensuring state exists for video %s...", video_file.uuid)
    state = video_file.get_or_create_state()
    logger.debug("Pipe 2 Step: State ensured for video %s.", video_file.uuid)
    return state

def _pipe_2_step_extract_frames(video_file: "VideoFile", state: "VideoState"):
    """Extracts frames if not already extracted."""
    if not state.frames_extracted:
        logger.info("Pipe 2 Step: Frames not extracted for video %s, extracting now...", video_file.uuid)
        video_file.extract_frames(overwrite=False) # Raises exceptions on failure
        logger.info("Pipe 2 Step: Frame extraction complete for video %s.", video_file.uuid)
    else:
        logger.info("Pipe 2 Step: Frames already extracted for video %s.", video_file.uuid)

def _pipe_2_step_anonymize(video_file: "VideoFile"):
    """Creates the anonymized video file."""
    logger.info("Pipe 2 Step: Creating anonymized video for %s...", video_file.uuid)
    video_file.anonymize(delete_original_raw=True) # Raises exceptions on failure
    logger.info("Pipe 2 Step: Anonymization complete for video %s.", video_file.uuid)

def _pipe_2_step_delete_sensitive_meta(video_file: "VideoFile"):
    """Deletes the associated SensitiveMeta object."""
    if video_file.sensitive_meta:
        logger.info("Pipe 2 Step: Deleting sensitive meta object for video %s...", video_file.uuid)
        try:
            sm_pk = video_file.sensitive_meta.pk
            video_file.sensitive_meta.delete()
            video_file.sensitive_meta = None # Important after SET_NULL
            video_file.save(update_fields=['sensitive_meta']) # Persist the null relation
            logger.info("Pipe 2 Step: Deleted sensitive meta object (PK: %s) for video %s.", sm_pk, video_file.uuid)
        except Exception as e:
            logger.error("Pipe 2 Step: Failed to delete sensitive meta object for video %s: %s", video_file.uuid, e, exc_info=True)
            # Raise exception to trigger transaction rollback
            raise RuntimeError(f"Failed to delete sensitive meta for video {video_file.uuid}") from e
    else:
        logger.info("Pipe 2 Step: No sensitive meta object found to delete for video %s.", video_file.uuid)

# --- Main Pipeline 2 Function ---
def _pipe_2(video_file:"VideoFile") -> bool:
    """
    Process the given video file through pipeline 2 operations: frame extraction (if needed),
    anonymization, and sensitive meta deletion, all within an atomic transaction.
    Orchestrates calls to step-based helper functions.
    Returns True on success, False on failure (logs error).
    """
    logger.info("Starting Pipe 2 for video %s", video_file.uuid)
    try:
        with transaction.atomic():
            # --- Call Step Functions ---
            state = _pipe_2_step_ensure_state(video_file)
            _pipe_2_step_extract_frames(video_file, state)
            _pipe_2_step_anonymize(video_file)
            _pipe_2_step_delete_sensitive_meta(video_file)
            # --- End Step Functions ---

            logger.info(f"Pipe 2 completed successfully for video {video_file.uuid}")
            # Transaction commits here if no exception occurred
            return True

    except (FileNotFoundError, ValueError, RuntimeError, Exception) as e:
        # Catch specific exceptions raised by steps, plus general Exception
        logger.error(f"Pipe 2 failed for video {video_file.uuid}: {e}", exc_info=True)
        # Transaction automatically rolls back on exception
        return False