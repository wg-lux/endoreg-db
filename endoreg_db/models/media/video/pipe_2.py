import logging
from typing import TYPE_CHECKING

from django.db import transaction

# Configure logging
logger = logging.getLogger(__name__)  # Changed from "video_file"

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState


def _pipe_2(video_file: "VideoFile") -> bool:
    """
    Process the given video file through pipeline 2 operations which include frame extraction,
    anonymization of the video, and deletion of sensitive meta data.
    Heavy I/O operations are performed outside the main atomic transaction for DB updates.

    Parameters:
        video_file (VideoFile): An instance of VideoFile representing the video to process.

    Returns:
        bool: True if all operations complete successfully; otherwise, False.
    """
    logger.info("Starting Pipe 2 for video %s", video_file.uuid)
    try:
        # --- Part 1: Frame Extraction ---
        # Determine if frames are needed (short transaction for state read)
        with transaction.atomic():
            state: "VideoState" = video_file.get_or_create_state()
            frames_needed = not state.frames_extracted

        if frames_needed:
            logger.info("Pipe 2: Frames not extracted. Extracting outside main DB transaction...")
            if not video_file.extract_frames(overwrite=False):  # Heavy I/O work
                logger.error("Pipe 2 failed: Frame extraction method returned False.")
                return False

            # Verify extraction and update state (short transaction)
            with transaction.atomic():
                video_file.refresh_from_db()
                if not video_file.state or not video_file.state.frames_extracted:
                    logger.error("Pipe 2 failed: Frame extraction did not update state successfully.")
                    return False
                logger.info("Pipe 2: Frame extraction complete.")
        else:
            logger.info("Pipe 2: Frames already extracted.")

        # --- Part 2: Video Anonymization ---
        # Determine if anonymization is needed (short transaction for state read)
        with transaction.atomic():
            state: "VideoState" = video_file.get_or_create_state()
            anonymization_needed = not state.anonymized
            if anonymization_needed:
                state.sensitive_meta_processed = False

        if anonymization_needed:
            logger.info("Pipe 2: Video not anonymized. Anonymizing outside main DB transaction...")
            anonymize_success = video_file.anonymize(delete_original_raw=True)  # Heavy I/O work
            if not anonymize_success:
                logger.error("Pipe 2 failed: Anonymization process failed (returned False).")
                return False

            # Verify anonymization and update state (short transaction)
            with transaction.atomic():
                video_file.refresh_from_db()
                if not video_file.state or not video_file.state.anonymized:
                    logger.error("Pipe 2 Error: State.anonymized is False even after anonymize() call.")
                    return False
                logger.info("Pipe 2: Anonymization complete.")
        else:
            logger.info("Pipe 2: Video already anonymized.")

        # --- Part 3: Final DB operations (now in its own atomic transaction) ---
        with transaction.atomic():
            video_file.refresh_from_db()  # Ensure we have the latest video_file state for these ops

            # Set sensitive_meta_processed True atomically
            state: "VideoState" = video_file.get_or_create_state()
            state.sensitive_meta_processed = True

            # Delete Sensitive Meta Object
            if video_file.sensitive_meta:
                logger.info("Pipe 2: Deleting sensitive meta object...")
                try:
                    sm_pk = video_file.sensitive_meta.pk
                    video_file.sensitive_meta.delete()
                    video_file.sensitive_meta = None  # Important after SET_NULL
                    video_file.save(update_fields=["sensitive_meta"])  # Persist the null relation
                    logger.info("Pipe 2: Deleted sensitive meta object (PK: %s).", sm_pk)
                except Exception as e:
                    logger.error("Pipe 2: Failed to delete sensitive meta object: %s", e, exc_info=True)
                    raise  # Reraise to ensure this transaction rolls back
            else:
                logger.info("Pipe 2: No sensitive meta object found to delete.")

            logger.info(f"Pipe 2 completed successfully for video {video_file.uuid}")
            return True

    except Exception as e:
        # This will catch exceptions from I/O operations if they raise,
        # or from the final transaction block, or any other unhandled error.
        logger.error(f"Pipe 2 failed for video {video_file.uuid} with unhandled exception: {e}", exc_info=True)
        return False
