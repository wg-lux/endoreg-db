import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import transaction

from endoreg_db.models.media.video.video_file_io import (
    _get_frame_dir_path,
    _get_temp_anonymized_frame_dir,
)
from endoreg_db.utils.file_operations import atomic_move_path, safe_rmtree

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, VideoState

logger = logging.getLogger(__name__)

__all__ = ["_delete_frames"]


def _get_staged_deletion_path(path: str) -> str:
    return f"{path}.pending_delete.{uuid.uuid4().hex}"


@transaction.atomic
def _delete_frames(video: "VideoFile") -> str:
    """
    Deletes extracted frame FILES ONLY. Resets relevant state flags atomically.
    Also cleans up temporary anonymization frame directories.
    Does NOT delete Frame objects from DB, but marks them as is_extracted=False.
    Raises RuntimeError if state update fails.
    """
    from endoreg_db.models.media.frame import Frame

    deleted_messages = []
    error_messages = []
    state_updated = False
    db_updated = False
    staged_directories: list[tuple[str, str]] = []

    frame_dir = _get_frame_dir_path(video)
    if frame_dir and frame_dir.exists():
        try:
            staged_frame_dir = frame_dir.with_name(
                _get_staged_deletion_path(frame_dir.name)
            )
            atomic_move_path(source=frame_dir, destination=staged_frame_dir)
            staged_directories.append((str(frame_dir), str(staged_frame_dir)))
            msg = f"Staged frame directory for deletion: {frame_dir}"
            logger.info(msg)
            deleted_messages.append(msg)
        except Exception as e:
            msg = f"Error staging frame directory {frame_dir} for deletion: {e}"
            logger.error(msg, exc_info=True)
            error_messages.append(msg)
    elif frame_dir:
        msg = f"Frame directory not found, skipping deletion: {frame_dir}"
        logger.debug(msg)
    else:
        msg = f"Frame directory path not set for video {video.video_hash}, cannot delete standard frames."
        logger.warning(msg)

    temp_anonym_frame_dir = None
    try:
        temp_anonym_frame_dir = _get_temp_anonymized_frame_dir(video)
        if temp_anonym_frame_dir and temp_anonym_frame_dir.exists():
            staged_temp_dir = temp_anonym_frame_dir.with_name(
                _get_staged_deletion_path(temp_anonym_frame_dir.name)
            )
            atomic_move_path(source=temp_anonym_frame_dir, destination=staged_temp_dir)
            staged_directories.append(
                (str(temp_anonym_frame_dir), str(staged_temp_dir))
            )
            msg = (
                "Staged temporary anonymized frame directory for deletion: "
                f"{temp_anonym_frame_dir}"
            )
            logger.info(msg)
            deleted_messages.append(msg)
    except Exception as e:
        msg = f"Error deleting temporary anonymized frame directory {temp_anonym_frame_dir}: {e}"
        logger.error(msg, exc_info=True)
        error_messages.append(msg)

    try:
        state: "VideoState" = video.get_or_create_state()
        update_fields_state = []
        if state.frames_extracted:
            state.frames_extracted = False
            update_fields_state.append("frames_extracted")

        if update_fields_state:
            state.save(update_fields=update_fields_state)
            logger.info(
                "Reset frame state flags (%s) for video %s.",
                ", ".join(update_fields_state),
                video.video_hash,
            )
            state_updated = True
        else:
            logger.info(
                "Frame state flags already False for video %s.", video.video_hash
            )
            state_updated = True

        try:
            update_count = Frame.objects.filter(video=video, is_extracted=True).update(
                is_extracted=False
            )
            if update_count > 0:
                logger.info(
                    "Marked %d Frame objects as is_extracted=False for video %s.",
                    update_count,
                    video.video_hash,
                )
            db_updated = True
        except Exception as db_err:
            msg = f"Failed to update is_extracted flag for Frame objects for video %s: {db_err}"
            logger.error(msg, exc_info=True)
            error_messages.append(msg)
            raise RuntimeError(
                "Failed to update extracted frame flags during frame deletion "
                f"for video {video.video_hash}"
            ) from db_err

    except Exception as state_e:
        for original_path_str, staged_path_str in reversed(staged_directories):
            original_path = Path(original_path_str)
            staged_path = Path(staged_path_str)
            if not staged_path.exists():
                continue
            try:
                atomic_move_path(source=staged_path, destination=original_path)
            except Exception as restore_exc:
                restore_msg = (
                    "Failed to restore staged frame directory "
                    f"{staged_path} -> {original_path}: {restore_exc}"
                )
                logger.error(restore_msg, exc_info=True)
                error_messages.append(restore_msg)
        msg = (
            f"Failed to update state after deleting frame files for video %s: {state_e}"
        )
        logger.error(msg, exc_info=True)
        error_messages.append(msg)
        raise RuntimeError(
            f"Failed to update state during frame file deletion for video {video.video_hash}"
        ) from state_e
    else:

        def _finalize_directory_cleanup() -> None:
            for _, staged_path_str in staged_directories:
                staged_path = Path(staged_path_str)
                if not staged_path.exists():
                    continue
                try:
                    safe_rmtree(staged_path, missing_ok=True)
                except Exception as cleanup_exc:
                    logger.error(
                        "Failed to finalize staged frame directory cleanup for %s: %s",
                        staged_path,
                        cleanup_exc,
                        exc_info=True,
                    )

        transaction.on_commit(_finalize_directory_cleanup)

    final_message = "; ".join(deleted_messages)
    if error_messages:
        final_message += "; Errors occurred: " + "; ".join(error_messages)
    elif state_updated and db_updated:
        final_message += "; State flags and Frame objects updated successfully."
    elif state_updated:
        final_message += "; State flags updated; Frame object update skipped or failed."
    else:
        final_message += "; State/Frame update skipped due to errors."

    return final_message
