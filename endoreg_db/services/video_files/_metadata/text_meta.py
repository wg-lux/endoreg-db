# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
import logging
from datetime import date
from typing import TYPE_CHECKING, Protocol, cast

# --- End Fix ---
from django.db import transaction
from lx_dtypes.models.contracts.video_text_metadata import VideoTextMetaPayload

# --- Fix Imports ---
from endoreg_db.models.metadata import SensitiveMeta
from endoreg_db.models.metadata.sensitive_meta_logic import (
    update_or_create_sensitive_meta_from_dict,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

    # SensitiveMeta is already imported above


class _VideoTextMetaState(Protocol):
    frames_extracted: bool
    text_meta_extracted: bool

    def refresh_from_db(self) -> None: ...

    def save(self, *, update_fields: list[str]) -> None: ...

    def mark_sensitive_meta_processed(self, *, save: bool = True) -> None: ...


logger = logging.getLogger(__name__)


def _update_text_metadata(
    video: "VideoFile",
    extracted_data_dict: VideoTextMetaPayload | None = None,
    ocr_frame_fraction: float = 0.01,
    cap: int = 10,
    overwrite: bool = False,
) -> "SensitiveMeta | None":
    """
    Extracts text from a fraction of video frames, updates or creates SensitiveMeta,
    and potentially updates the VideoFile's date field. Requires frames to be extracted.
    Raises ValueError if pre-conditions not met, RuntimeError on processing failure.

    State Transitions:
        - Post-condition: Sets state.text_meta_extracted=True (even if no text found).
    """
    logger.debug(f"Updating text metadata for video {video.video_hash}")
    state = cast(_VideoTextMetaState, video.get_or_create_state())
    state.refresh_from_db()

    if state.text_meta_extracted and not overwrite:
        logger.info(
            "Text already extracted for video %s and overwrite=False. Skipping.",
            video.video_hash,
        )  # Changed to info
        return video.sensitive_meta  # Return existing meta if available
    # --- End Pre-condition Checks ---

    # Extract text using the AI helper function
    # _extract_text_from_video_frames raises ValueError on pre-condition failure
    try:
        if not extracted_data_dict:
            extracted_text_payload = video.extract_text_from_frames(
                frame_fraction=ocr_frame_fraction, cap=cap
            )
            extracted_data_dict = (
                VideoTextMetaPayload.model_validate(extracted_text_payload)
                if extracted_text_payload is not None
                else None
            )
    except Exception as text_extract_e:
        logger.error(
            "Failed during text extraction step for video %s: %s",
            video.video_hash,
            text_extract_e,
            exc_info=True,
        )
        raise RuntimeError(
            f"Text extraction failed for video {video.video_hash}"
        ) from text_extract_e

    # --- Atomic Update Block ---
    try:
        with transaction.atomic():
            # Refresh state in case it changed
            state.refresh_from_db()
            sensitive_meta_instance = video.sensitive_meta  # Get current instance

            if not extracted_data_dict:
                logger.warning(
                    "No text extracted for video %s; skipping SensitiveMeta update.",
                    video.video_hash,
                )
                # Mark state as retrieved even if no data found, to avoid re-running unless overwrite=True
                if not state.text_meta_extracted:
                    state.text_meta_extracted = True
                    state.save(update_fields=["text_meta_extracted"])
                return sensitive_meta_instance  # Return existing meta if available

            # Add center info if not already present in extracted data
            extracted_data_dict = (
                extracted_data_dict.model_copy(
                    update={"center_name": video.center.name}
                )
                if "center_name" not in extracted_data_dict.root and video.center
                else extracted_data_dict
            )
            logger.debug(
                "Data for SensitiveMeta update for video %s: %s",
                video.video_hash,
                extracted_data_dict.model_dump(mode="python"),
            )

            # Pass the Class, the data dict, and the current instance (or None)
            # This function might raise exceptions if data is invalid
            sensitive_meta, created = update_or_create_sensitive_meta_from_dict(
                SensitiveMeta,  # Pass the class
                extracted_data_dict.to_dict(),
                instance=sensitive_meta_instance,  # Pass current instance via keyword
            )

            # Update VideoFile fields if necessary
            update_fields_video: list[str] = []
            if (
                created or sensitive_meta != sensitive_meta_instance
            ):  # Check if relation needs update
                video.sensitive_meta = sensitive_meta
                update_fields_video.append("sensitive_meta")

            extracted_date = extracted_data_dict.root.get("date")
            if not video.date and sensitive_meta and isinstance(extracted_date, date):
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
                logger.info(
                    f"Marked sensitive_meta_processed=True for video {video.video_hash} after text metadata update"
                )

            logger.info(
                "Successfully updated/created SensitiveMeta and state for video %s.",
                video.video_hash,
            )  # Changed to info
            return sensitive_meta

    except Exception as e:
        logger.error(
            "Failed to update/create SensitiveMeta or state for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        # Re-raise exception for the pipeline to catch
        raise RuntimeError(
            f"Failed to update/create SensitiveMeta or state for video {video.video_hash}"
        ) from e
    # --- End Atomic Update Block ---
