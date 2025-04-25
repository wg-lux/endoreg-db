import logging
from typing import TYPE_CHECKING
from django.db import transaction


# Configure logging
logger = logging.getLogger(__name__) # Changed from "video_file"

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState

def _pipe_2(video_file:"VideoFile") -> bool:
    """
    Process the given video file through pipeline 2 operations: frame extraction (if needed),
    anonymization, and sensitive meta deletion, all within an atomic transaction.

    State Transitions:
        - Calls extract_frames (sets frames_extracted, frames_initialized if needed).
        - Calls anonymize (requires frames_extracted, sets anonymized, schedules cleanup).
        - Deletes SensitiveMeta.
    """
    logger.info("Starting Pipe 2 for video %s", video_file.uuid)
    try:
        with transaction.atomic():
            # 1. Extract Frames (if not already extracted)
            state: "VideoState" = video_file.get_or_create_state() # Use state helper
            if not state.frames_extracted:
                logger.info("Pipe 2: Frames not extracted, extracting now...")
                # extract_frames handles state checks/updates and returns bool
                if not video_file.extract_frames(overwrite=False):
                     logger.error("Pipe 2 failed: Frame extraction method returned False.")
                     return False # Transaction rollback
                # No need to check state here, extract_frames guarantees it or returns False
                logger.info("Pipe 2: Frame extraction complete.")
            else:
                logger.info("Pipe 2: Frames already extracted.")

            # 2. Create Anonymized Video File
            logger.info("Pipe 2: Creating anonymized video...")
            # anonymize() requires frames_extracted (guaranteed by step 1)
            # anonymize() handles setting state.anonymized and returns bool
            if not video_file.anonymize(delete_original_raw=True):
                    logger.error("Pipe 2 failed: Anonymization process failed (returned False).")
                    return False # Transaction rollback
            logger.info("Pipe 2: Anonymization complete.")
            # No need to check state.anonymized here, anonymize guarantees it or returns False

            # 3. Delete Sensitive Meta Object
            if video_file.sensitive_meta:
                logger.info("Pipe 2: Deleting sensitive meta object...")
                try:
                    sm_pk = video_file.sensitive_meta.pk
                    video_file.sensitive_meta.delete()
                    video_file.sensitive_meta = None # Important after SET_NULL
                    video_file.save(update_fields=['sensitive_meta']) # Persist the null relation
                    logger.info("Pipe 2: Deleted sensitive meta object (PK: %s).", sm_pk)
                except Exception as e:
                    logger.error("Pipe 2: Failed to delete sensitive meta object: %s", e, exc_info=True)
                    # Decide if this is a critical failure
                    return False # Rollback transaction
            else:
                logger.info("Pipe 2: No sensitive meta object found to delete.")

            logger.info(f"Pipe 2 completed successfully for video {video_file.uuid}")
            # Transaction commits here if no exception occurred
            return True

    except Exception as e:
        # Transaction automatically rolls back on exception
        logger.error(f"Pipe 2 failed for video {video_file.uuid} with unhandled exception: {e}", exc_info=True)
        return False