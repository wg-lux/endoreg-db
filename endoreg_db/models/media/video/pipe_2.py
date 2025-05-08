import logging
from typing import TYPE_CHECKING
from django.db import transaction


# Configure logging
logger = logging.getLogger(__name__) # Changed from "video_file"

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState

def _pipe_2(video_file:"VideoFile") -> bool:
    """
    Process the given video file through pipeline 2 operations which include frame extraction,
    anonymization of the video, and deletion of sensitive meta data, all within an atomic transaction.

    Parameters:
        video_file (VideoFile): An instance of VideoFile representing the video to process.

    Returns:
        bool: True if all operations complete successfully; otherwise, False.

    Workflow Details:
        1. Frame Extraction:
           - Checks if frames have already been extracted for the video.
           - If not, triggers the frame extraction process. If extraction fails or the state is not
             correctly updated, the process is halted and False is returned.
        2. Video Anonymization:
           - Initiates anonymization, which includes cleaning up database fields and scheduling file cleanup.
           - After anonymization, verifies that the video's state reflects the changes. Failure to confirm
             the anonymized state results in the function returning False.
        3. Sensitive Meta Deletion:
           - If the video has an associated sensitive meta object, it is deleted and the relationship is
             cleared in the database.
           - Any exception during deletion results in the entire transaction rolling back and the function
             returning False.

    If any unexpected exception occurs during the process, the transaction is rolled back and the function
    returns False, ensuring data consistency.
    """
    logger.info("Starting Pipe 2 for video %s", video_file.uuid)
    try:
        with transaction.atomic():
            # 1. Extract Frames Outside the Main Transaction
            with transaction.atomic():
                state: "VideoState" = video_file.get_or_create_state()  # Quick lookup within a short txn
                frames_needed = not state.frames_extracted

            if frames_needed:
                logger.info("Pipe 2: Frames not extracted. Extracting outside transaction...")
                if not video_file.extract_frames(overwrite=False):  # Heavy I/O work outside txn
                    logger.error("Pipe 2 failed: Frame extraction method returned False.")
                    return False

                with transaction.atomic():
                    state.refresh_from_db()  # Confirm state update within a new txn
                    if not state.frames_extracted:
                        logger.error("Pipe 2 failed: Frame extraction did not update state successfully.")
                        return False
                    logger.info("Pipe 2: Frame extraction complete.")
            else:
                logger.info("Pipe 2: Frames already extracted.")

            # 2. Create Anonymized Video File Outside the Main Transaction
            logger.info("Pipe 2: Creating anonymized video outside transaction...")
            anonymize_success = video_file.anonymize(delete_original_raw=True)  # Heavy work outside txn
            if not anonymize_success:
                logger.error("Pipe 2 failed: Anonymization process failed (returned False).")
                return False

            with transaction.atomic():
                state.refresh_from_db()  # Verify update of anonymized state in a quick txn
                if not state.anonymized:
                    logger.error("Pipe 2 Error: State.anonymized is False even after anonymize() call.")
                    return False
                logger.info("Pipe 2: Anonymization complete.")

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