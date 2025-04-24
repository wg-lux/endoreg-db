import logging
from typing import TYPE_CHECKING


# Configure logging
logger = logging.getLogger("video_file")

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState

def _pipe_2(video_file:"VideoFile") -> bool:
    """
    Pipeline 2: Ensure frames, anonymize, delete raw frames, delete raw video, delete sensitive meta.
    """
    logger.info(f"Starting Pipe 2 for video {video_file.uuid}")
    try:
        # 1. Extract Frames (if not already extracted)
        state: "VideoState" = video_file.get_or_create_state() # Use state helper
        if not state.frames_extracted:
            logger.info("Pipe 2: Frames not extracted, extracting now...")
            if not video_file.extract_frames(overwrite=False): # Check return value
                 logger.error("Pipe 2 failed: Frame extraction method returned False.")
                 return False
            state.refresh_from_db() # Refresh state after extraction
            if not state.frames_extracted: # Double check state after refresh
                logger.error("Pipe 2 failed: Frame extraction did not update state successfully.")
                return False
            logger.info("Pipe 2: Frame extraction complete.")
        else:
            logger.info("Pipe 2: Frames already extracted.")

        # 2. Create Anonymized Video File
        logger.info("Pipe 2: Creating anonymized video...")
        # anonymize() handles clearing DB fields, setting state.anonymized,
        # and scheduling physical file cleanup via on_commit
        anonymize_success = video_file.anonymize(delete_original_raw=True)
        if not anonymize_success:
                # Error should be logged within anonymize() if it returns False
                logger.error("Pipe 2 failed: Anonymization process failed (returned False).")
                return False
        logger.info("Pipe 2: Anonymization complete.")
        # Verify state immediately after successful anonymize call
        state.refresh_from_db()
        if not state.anonymized:
            logger.error("Pipe 2 Error: State.anonymized is False even after successful anonymize() call.")
            # This indicates a problem within anonymize() not setting the state
            return False


        # --- REMOVED REDUNDANT CALL ---
        # 3. Delete Raw Frames (Original frame *files* - DB objects remain)
        # Note: _cleanup_raw_assets scheduled by anonymize already deletes the frame dir.
        # --- END REMOVED CALL ---


        # --- REMOVED REDUNDANT CALL ---
        # 4. Delete Raw Video File (Handled by anonymize's on_commit hook)
        # --- END REMOVED CALL ---

        # 5. Delete Sensitive Meta Object
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
                return False
        else:
            logger.info("Pipe 2: No sensitive meta object found to delete.")


        logger.info(f"Pipe 2 completed successfully for video {video_file.uuid}")
        return True

    except Exception as e:
        logger.error(f"Pipe 2 failed for video {video_file.uuid} with unhandled exception: {e}", exc_info=True)
        return False