import logging
import json
from typing import TYPE_CHECKING

from django.db import transaction
from endoreg_db.models.media.video.video_file_frames._extract_frames import (
    validate_video_frame_cache,
)

# Configure logging
logger = logging.getLogger(__name__)  # Changed from "video_file"

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile


def _record_frame_cache_mismatch(video_file: "VideoFile", detail: str) -> None:
    state = video_file.get_or_create_state()
    if state.frames_extracted:
        state.mark_frames_not_extracted(save=True)
    try:
        from endoreg_db.services.media_integrity import mark_video_integrity_lost

        mark_video_integrity_lost(video_file, detail)
    except Exception as exc:
        logger.error(
            "Failed to mark video %s integrity lost after frame cache mismatch: %s",
            video_file.video_hash,
            exc,
            exc_info=True,
        )


def _video_integrity_failure_detail(video_file: "VideoFile") -> str:
    payload = video_file.meta if isinstance(video_file.meta, dict) else {}
    detail = str(payload.get("integrity_error") or "").strip()
    if detail:
        return detail
    if bool(getattr(getattr(video_file, "state", None), "processing_error", False)):
        return "video state is marked failed/lost"
    return ""


def _video_has_integrity_failure(video_file: "VideoFile") -> bool:
    payload = video_file.meta if isinstance(video_file.meta, dict) else {}
    return payload.get("integrity_status") == "lost" or bool(
        getattr(getattr(video_file, "state", None), "processing_error", False)
    )


def _ensure_valid_frame_cache_for_legacy_pipe_2(video_file: "VideoFile") -> bool:
    validation = validate_video_frame_cache(video_file)
    if validation.valid:
        return True

    logger.warning(
        json.dumps(
            {
                "event": "pipe_2_frame_cache_preflight",
                "video_hash": str(video_file.video_hash),
                "status": "invalid",
                **validation.as_log_payload(),
            },
            sort_keys=True,
            default=str,
        )
    )

    try:
        if not video_file.extract_frames(overwrite=False):
            detail = "legacy pipe_2 frame cache repair returned false"
            _record_frame_cache_mismatch(video_file, detail)
            logger.error("Pipe 2 failed: %s.", detail)
            return False
    except Exception as exc:
        detail = f"legacy pipe_2 frame cache repair failed: {exc}"
        _record_frame_cache_mismatch(video_file, detail)
        logger.error(
            "Pipe 2 failed: could not repair frame cache for video %s: %s",
            video_file.video_hash,
            exc,
            exc_info=True,
        )
        return False

    validation = validate_video_frame_cache(video_file)
    if validation.valid:
        return True

    detail = "legacy pipe_2 frame cache remains invalid after repair"
    _record_frame_cache_mismatch(video_file, detail)
    logger.error(
        json.dumps(
            {
                "event": "pipe_2_frame_cache_preflight",
                "video_hash": str(video_file.video_hash),
                "status": "invalid_after_repair",
                **validation.as_log_payload(),
            },
            sort_keys=True,
            default=str,
        )
    )
    return False


def _clear_invalid_frame_cache_flag_for_streaming_pipe_2(
    video_file: "VideoFile",
) -> bool:
    state = video_file.get_or_create_state()
    if not state.frames_extracted:
        return True

    try:
        validation = validate_video_frame_cache(video_file)
    except Exception as exc:
        logger.warning(
            "Pipe 2: Could not validate existing frame cache for video %s before "
            "streamed anonymization. Clearing frames_extracted without repairing: %s",
            video_file.video_hash,
            exc,
            exc_info=True,
        )
        state.mark_frames_not_extracted(save=True)
        return True

    if validation.valid:
        return True

    if not video_file.has_raw:
        detail = (
            "legacy pipe_2 frame cache invalid and raw media unavailable for "
            "streamed anonymization"
        )
        _record_frame_cache_mismatch(video_file, detail)
        logger.error(
            json.dumps(
                {
                    "event": "pipe_2_frame_cache_preflight",
                    "video_hash": str(video_file.video_hash),
                    "status": "invalid_no_raw_media",
                    **validation.as_log_payload(),
                },
                sort_keys=True,
                default=str,
            )
        )
        return False

    logger.warning(
        json.dumps(
            {
                "event": "pipe_2_frame_cache_preflight",
                "video_hash": str(video_file.video_hash),
                "status": "invalid_ignored_for_streaming_anonymization",
                **validation.as_log_payload(),
            },
            sort_keys=True,
            default=str,
        )
    )
    state.mark_frames_not_extracted(save=True)
    return True


def _pipe_2(video_file: "VideoFile") -> bool:
    """
    Process the given video file through pipeline 2 operations: streamed video
    anonymization and deletion of sensitive meta data.
    Heavy I/O operations are performed outside the main atomic transaction for DB updates.

    Parameters:
        video_file (VideoFile): An instance of VideoFile representing the video to process.

    Returns:
        bool: True if all operations complete successfully; otherwise, False.
    """
    logger.info("Starting Pipe 2 for video %s", video_file.video_hash)
    try:
        if _video_has_integrity_failure(video_file):
            logger.error(
                json.dumps(
                    {
                        "event": "pipe_2_refused_lost_media",
                        "video_id": video_file.pk,
                        "video_hash": str(video_file.video_hash),
                        "failure_reason": _video_integrity_failure_detail(video_file),
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            return False

        if not _clear_invalid_frame_cache_flag_for_streaming_pipe_2(video_file):
            return False

        # --- Part 2: Video Anonymization ---
        # Determine if anonymization is needed (short transaction for state read)
        with transaction.atomic():
            state = video_file.get_or_create_state()
            anonymization_needed = not state.anonymized
            if anonymization_needed:
                state.sensitive_meta_processed = False

        if anonymization_needed:
            logger.info(
                "Pipe 2: Video not anonymized. Anonymizing outside main DB transaction..."
            )
            anonymize_success = video_file.anonymize(
                delete_original_raw=True
            )  # Heavy I/O work
            if not anonymize_success:
                logger.error(
                    "Pipe 2 failed: Anonymization process failed (returned False)."
                )
                return False

            # Verify anonymization and update state (short transaction)
            with transaction.atomic():
                video_file.refresh_from_db()
                if not video_file.state or not video_file.state.anonymized:
                    logger.error(
                        "Pipe 2 Error: State.anonymized is False even after anonymize() call."
                    )
                    return False
                logger.info("Pipe 2: Anonymization complete.")
        else:
            logger.info("Pipe 2: Video already anonymized.")

        # --- Part 3: Final DB operations (now in its own atomic transaction) ---
        with transaction.atomic():
            video_file.refresh_from_db()  # Ensure we have the latest video_file state for these ops

            # Set sensitive_meta_processed True atomically
            state.sensitive_meta_processed = True

            # Delete Sensitive Meta Object
            if video_file.sensitive_meta:
                logger.info("Pipe 2: Deleting sensitive meta object...")
                try:
                    sm_pk = video_file.sensitive_meta.pk
                    video_file.sensitive_meta.delete()
                    video_file.sensitive_meta = None  # Important after SET_NULL
                    video_file.save(
                        update_fields=["sensitive_meta"]
                    )  # Persist the null relation
                    logger.info(
                        "Pipe 2: Deleted sensitive meta object (PK: %s).", sm_pk
                    )
                except Exception as e:
                    logger.error(
                        "Pipe 2: Failed to delete sensitive meta object: %s",
                        e,
                        exc_info=True,
                    )
                    raise  # Reraise to ensure this transaction rolls back
            else:
                logger.info("Pipe 2: No sensitive meta object found to delete.")

            logger.info(
                f"Pipe 2 completed successfully for video {video_file.video_hash}"
            )
            return True

    except Exception as e:
        # This will catch exceptions from I/O operations if they raise,
        # or from the final transaction block, or any other unhandled error.
        logger.error(
            f"Pipe 2 failed for video {video_file.video_hash} with unhandled exception: {e}",
            exc_info=True,
        )
        return False
