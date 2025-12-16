import logging
from typing import TYPE_CHECKING, Set

from django.db import transaction

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


@transaction.atomic
def _mark_frames_extracted_status(video: "VideoFile", extracted_frame_numbers: Set[int], status: bool):
    """
    Bulk updates the is_extracted status for a set of frame numbers.
    """
    from endoreg_db.models.media.frame import Frame

    if not extracted_frame_numbers:
        logger.warning("No frame numbers provided to update status for video %s.", video.uuid)
        return 0

    # --- Enhanced Logging ---
    min_frame = min(extracted_frame_numbers) if extracted_frame_numbers else "N/A"
    max_frame = max(extracted_frame_numbers) if extracted_frame_numbers else "N/A"
    contains_zero = 0 in extracted_frame_numbers
    logger.info(
        "Attempting to mark %d Frame objects as is_extracted=%s for video %s. Frame numbers range: [%s-%s]. Contains frame 0: %s",
        len(extracted_frame_numbers),
        status,
        video.uuid,
        min_frame,
        max_frame,
        contains_zero,
    )
    # --- End Enhanced Logging ---

    try:
        # Update Frame objects based on frame_number
        # Convert set to list for potentially better compatibility with some DB backends
        updated_count = Frame.objects.filter(video=video, frame_number__in=list(extracted_frame_numbers)).update(is_extracted=status)

        logger.info("Database reported updating %d Frame objects to is_extracted=%s for video %s.", updated_count, status, video.uuid)

        # Verification step
        if updated_count != len(extracted_frame_numbers):
            logger.warning(
                "Mismatch during status update for video %s. Expected to update %d frames, but DB reported updating %d.",
                video.uuid,
                len(extracted_frame_numbers),
                updated_count,
            )
            # --- Add detailed check for frame 0 if status is True and it should have been updated ---
            if status is True and contains_zero and updated_count < len(extracted_frame_numbers):
                try:
                    # Check the status of frame 0 directly after the update attempt
                    frame_zero = Frame.objects.get(video_file=video, frame_number=0)
                    if not frame_zero.is_extracted:
                        logger.error("Verification check: Frame 0 (PK: %s) was NOT updated to is_extracted=True for video %s.", frame_zero.pk, video.uuid)
                    else:
                        # This case should ideally not happen if updated_count < expected count, but log just in case
                        logger.info(
                            "Verification check: Frame 0 (PK: %s) IS is_extracted=True for video %s, despite count mismatch.", frame_zero.pk, video.uuid
                        )
                except Frame.DoesNotExist:
                    logger.error("Verification check: Frame 0 does not exist for video %s during status check.", video.uuid)
                except Exception as verify_e:
                    logger.error("Verification check: Error checking frame 0 status for video %s: %s", video.uuid, verify_e)
            # --- End detailed check ---

        return updated_count
    except Exception as e:
        logger.error("Failed to bulk update is_extracted status for video %s: %s", video.uuid, e, exc_info=True)
        raise  # Re-raise to ensure transaction rollback if needed
