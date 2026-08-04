import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import transaction

from endoreg_db.services.video_files._io import (
    _get_frame_dir_path,
    _get_temp_anonymized_frame_dir,
)
from endoreg_db.utils.file_operations import (
    atomic_move_path,
    safe_rmtree,
    safe_unlink_file,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile, VideoState

logger = logging.getLogger(__name__)

__all__ = ["_delete_frames"]


def _get_staged_deletion_path(path: str) -> str:
    return f"{path}.pending_delete.{uuid.uuid4().hex}"


def _dataset_backed_frame_ids_with_files(
    video: "VideoFile",
) -> tuple[set[int], set[Path]]:
    from endoreg_db.models.media.frame import Frame

    frame_ids: set[int] = set()
    frame_paths: set[Path] = set()
    frames = (
        Frame.objects.filter(
            video=video,
            image_classification_annotations__image_ai_datasets__isnull=False,
        )
        .distinct()
        .select_related("video")
    )
    for frame in frames:
        frame_path = frame.file_path
        if not frame_path.is_file():
            continue
        frame_ids.add(frame.pk)
        frame_paths.add(frame_path.resolve())
    return frame_ids, frame_paths


def _delete_non_dataset_frame_files(frame_dir: Path, preserved_paths: set[Path]) -> int:
    deleted_count = 0
    for frame_path in frame_dir.rglob("*"):
        if not frame_path.is_file():
            continue
        if frame_path.resolve() in preserved_paths:
            continue
        safe_unlink_file(frame_path, missing_ok=True)
        deleted_count += 1
    return deleted_count


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
    cleanup_directories: list[Path] = []
    dataset_frame_ids, dataset_frame_paths = _dataset_backed_frame_ids_with_files(video)

    frame_dir = _get_frame_dir_path(video)
    if frame_dir and frame_dir.exists():
        if dataset_frame_paths:
            msg = (
                "Preserving dataset-backed frame files while deleting other frame "
                f"files in directory: {frame_dir}"
            )
        else:
            cleanup_directories.append(frame_dir)
            msg = f"Scheduled frame directory for deletion: {frame_dir}"
        logger.info(msg)
        deleted_messages.append(msg)
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
            cleanup_directories.append(temp_anonym_frame_dir)
            msg = (
                "Scheduled temporary anonymized frame directory for deletion: "
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
            extracted_frames = Frame.objects.filter(video=video, is_extracted=True)
            if dataset_frame_ids:
                update_count = extracted_frames.exclude(
                    pk__in=dataset_frame_ids
                ).update(is_extracted=False)
                preserved_count = Frame.objects.filter(pk__in=dataset_frame_ids).update(
                    is_extracted=True
                )
                logger.info(
                    "Preserved %d dataset-backed extracted Frame objects for video %s.",
                    preserved_count,
                    video.video_hash,
                )
            else:
                update_count = extracted_frames.update(is_extracted=False)
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
            if frame_dir and frame_dir.exists() and dataset_frame_paths:
                try:
                    deleted_count = _delete_non_dataset_frame_files(
                        frame_dir,
                        dataset_frame_paths,
                    )
                    logger.info(
                        "Deleted %d non-dataset frame files for video %s while preserving dataset-backed frames.",
                        deleted_count,
                        video.video_hash,
                    )
                except Exception as cleanup_exc:
                    logger.error(
                        "Failed to delete non-dataset frame files for %s: %s",
                        frame_dir,
                        cleanup_exc,
                        exc_info=True,
                    )
            for original_path in cleanup_directories:
                if not original_path.exists():
                    continue
                staged_path = original_path.with_name(
                    _get_staged_deletion_path(original_path.name)
                )
                try:
                    atomic_move_path(source=original_path, destination=staged_path)
                    safe_rmtree(staged_path, missing_ok=True)
                except Exception as cleanup_exc:
                    if staged_path.exists() and not original_path.exists():
                        try:
                            atomic_move_path(
                                source=staged_path,
                                destination=original_path,
                            )
                        except Exception as restore_exc:
                            logger.error(
                                "Failed to restore staged frame directory "
                                "%s -> %s after cleanup failure: %s",
                                staged_path,
                                original_path,
                                restore_exc,
                                exc_info=True,
                            )
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
