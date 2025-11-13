import logging
from typing import TYPE_CHECKING, Optional

# --- End Fix ---
from django.db import transaction

# --- Fix Imports ---
from ....metadata import SensitiveMeta
from ....metadata.sensitive_meta_logic import update_or_create_sensitive_meta_from_dict

if TYPE_CHECKING:
    from ..video_file import VideoFile
    # SensitiveMeta is already imported above

logger = logging.getLogger(__name__)


def _update_text_metadata(
    video: "VideoFile",
    extracted_data_dict: Optional[dict] = None,
    ocr_frame_fraction: float = 0.01,
    cap: int = 10,
    overwrite: bool = False,
) -> Optional["SensitiveMeta"]:
    """
    Extracts text from a fraction of video frames, updates or creates SensitiveMeta,
    and potentially updates the VideoFile's date field. Requires frames to be extracted.
    Raises ValueError if pre-conditions not met, RuntimeError on processing failure.

    State Transitions:
        - Pre-condition: Requires state.frames_extracted=True.
        - Post-condition: Sets state.text_meta_extracted=True (even if no text found).
    """
    logger.debug(f"Updating text metadata for video {video.uuid}")
    state = video.get_or_create_state()

    # --- Pre-condition Checks ---
    if not state.frames_extracted:
        # Attempt to extract frames automatically if they're not available
        logger.warning(f"Frames not extracted for video {video.uuid}. Attempting automatic frame extraction...")
        try:
            success = video.extract_frames(overwrite=False)
        except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
            logger.error(
                "Failed to extract frames for video %s: %s",
                video.uuid,
                exc,
                exc_info=True,
            )
            raise ValueError(f"Cannot update text metadata for video {video.uuid}: Frames not extracted and automatic extraction failed") from exc

        if not success:
            raise ValueError(f"Cannot update text metadata for video {video.uuid}: Frame extraction returned False")

        state.refresh_from_db()
        if not state.frames_extracted:
            raise ValueError(f"Cannot update text metadata for video {video.uuid}: Frame extraction completed but state was not updated")

        logger.info(f"Successfully extracted frames for video {video.uuid}")

    if state.text_meta_extracted and not overwrite:
        logger.info("Text already extracted for video %s and overwrite=False. Skipping.", video.uuid)  # Changed to info
        return video.sensitive_meta  # Return existing meta if available
    # --- End Pre-condition Checks ---

    # Extract text using the AI helper function
    # _extract_text_from_video_frames raises ValueError on pre-condition failure
    try:
        if not extracted_data_dict:
            extracted_data_dict = video.extract_text_from_frames(frame_fraction=ocr_frame_fraction, cap=cap)
    except Exception as text_extract_e:
        logger.error("Failed during text extraction step for video %s: %s", video.uuid, text_extract_e, exc_info=True)
        raise RuntimeError(f"Text extraction failed for video {video.uuid}") from text_extract_e

    # --- Atomic Update Block ---
    try:
        with transaction.atomic():
            # Refresh state in case it changed
            state.refresh_from_db()
            sensitive_meta_instance = video.sensitive_meta  # Get current instance

            if not extracted_data_dict:
                logger.warning("No text extracted for video %s; skipping SensitiveMeta update.", video.uuid)
                # Mark state as retrieved even if no data found, to avoid re-running unless overwrite=True
                if not state.text_meta_extracted:
                    state.text_meta_extracted = True
                    state.save(update_fields=["text_meta_extracted"])
                return sensitive_meta_instance  # Return existing meta if available

            # Add center info if not already present in extracted data
            if "center_name" not in extracted_data_dict and video.center:
                extracted_data_dict["center_name"] = video.center.name
            logger.debug("Data for SensitiveMeta update for video %s: %s", video.uuid, extracted_data_dict)

            # Pass the Class, the data dict, and the current instance (or None)
            # This function might raise exceptions if data is invalid
            sensitive_meta, created = update_or_create_sensitive_meta_from_dict(
                SensitiveMeta,  # Pass the class
                extracted_data_dict,
                instance=sensitive_meta_instance,  # Pass current instance via keyword
            )

            # Update VideoFile fields if necessary
            update_fields_video = []
            if created or sensitive_meta != sensitive_meta_instance:  # Check if relation needs update
                video.sensitive_meta = sensitive_meta
                update_fields_video.append("sensitive_meta")

            if not video.date and sensitive_meta and extracted_data_dict.get("date"):
                extracted_date = extracted_data_dict.get("date")
                if extracted_date:  # Ensure date is not None or empty
                    video.date = extracted_date
                    update_fields_video.append("date")
                    

            # Save VideoFile if fields changed
            if update_fields_video:
                video.save(update_fields=update_fields_video)

            # Update state
            if not state.text_meta_extracted:
                state.text_meta_extracted = True
                state.save(update_fields=["text_meta_extracted"])

            # Mark sensitive meta as processed when updated via text metadata
            if sensitive_meta:
                state.mark_sensitive_meta_processed(save=True)
                logger.info(f"Marked sensitive_meta_processed=True for video {video.uuid} after text metadata update")

            logger.info("Successfully updated/created SensitiveMeta and state for video %s.", video.uuid)  # Changed to info
            return sensitive_meta

    except Exception as e:
        logger.error("Failed to update/create SensitiveMeta or state for video %s: %s", video.uuid, e, exc_info=True)
        # Re-raise exception for the pipeline to catch
        raise RuntimeError(f"Failed to update/create SensitiveMeta or state for video {video.uuid}") from e
    # --- End Atomic Update Block ---
