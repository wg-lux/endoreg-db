import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from django.db import transaction

from endoreg_db.utils.paths import data_paths

# --- Aligned Imports ---
from endoreg_db.utils.file_operations import safe_unlink_file
from endoreg_db.utils.storage import delete_field_file, ensure_local_file, file_exists

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger("video_file")


def _get_raw_file_path(video: "VideoFile") -> Optional[Path]:
    """Return the best-effort absolute path to the raw video on disk."""
    if not (video.has_raw and getattr(video.raw_file, "name", None)):
        return None

    streamable_relative_path = getattr(video, "streamable_relative_path", "")
    if streamable_relative_path:
        streamable_candidate = data_paths.storage / streamable_relative_path
        if streamable_candidate.is_file():
            return streamable_candidate.resolve()

    # 1) Canonical: use Django's storage path
    try:
        direct_path = Path(video.raw_file.path)
        if direct_path.is_file():
            return direct_path.resolve()
        else:
            logger.debug(
                "raw_file.path for video %s is not a regular file: %s",
                video.video_hash,
                direct_path,
            )
    except Exception as exc:
        logger.debug(
            "Could not access raw_file.path for video %s (may be remote storage): %s",
            video.video_hash,
            exc,
        )

    # 2) Fallback: use just the filename and search in known dirs
    raw_rel = Path(video.raw_file.name)
    filename = raw_rel.name  # strip any (possibly wrong) prefix

    candidates = [
        data_paths["import_video"] / filename,
        data_paths["sensitive_video"] / filename,
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    logger.warning(
        "Raw video file '%s' not found locally for video %s.",
        video.raw_file.name,
        video.video_hash,
    )
    return None


@contextmanager
def _ensure_local_raw_file(video: "VideoFile") -> Iterator[Path]:
    """Yield a local filesystem path for the raw file, downloading if required."""
    if not video.has_raw:
        raise ValueError(f"Video {video.video_hash} has no raw file")

    with ensure_local_file(video.raw_file) as local_path:
        yield local_path


def _get_processed_file_path(video: "VideoFile") -> Optional[Path]:
    """Returns the absolute Path object for the processed file, if it exists locally."""
    processed_field = getattr(video, "processed_file", None)
    if not (video.is_processed and processed_field and processed_field.name):
        return None

    processed_name = str(processed_field.name)
    try:
        direct_path = Path(processed_field.path)
        if direct_path.exists():
            return direct_path.resolve()
    except Exception as exc:
        logger.debug(
            "Could not access direct processed_file.path for video %s: %s",
            video.video_hash,
            exc,
        )

    if processed_name:
        candidate = (
            Path(processed_name)
            if processed_name.startswith("/")
            else data_paths.storage / processed_name
        )
        if candidate.exists():
            return candidate.resolve()

    # --- Use aligned file_exists helper ---
    if processed_field and file_exists(processed_field):
        logger.debug(
            "Processed file for %s available only via remote storage backend",
            video.video_hash,
        )
    else:
        logger.warning(
            "Could not get path for processed file of VideoFile %s: path unavailable",
            video.video_hash,
        )
    return None


@contextmanager
def _ensure_local_processed_file(video: "VideoFile") -> Iterator[Path]:
    """Yield a local path to the processed file, downloading if necessary."""
    if not video.is_processed:
        raise ValueError(f"Video {video.video_hash} has no processed file")

    with ensure_local_file(video.processed_file) as local_path:
        yield local_path


@transaction.atomic
def _delete_with_file(video: "VideoFile", *args, **kwargs):
    """Deletes the VideoFile record and its associated physical files (raw, processed, frames)."""
    # 1. Delete Frames
    try:
        frame_delete_msg = video.delete_frames()
        logger.info(
            "Frame deletion result for video %s: %s", video.video_hash, frame_delete_msg
        )
    except Exception as frame_del_e:
        logger.error(
            "Error during frame file/state deletion for video %s: %s",
            video.video_hash,
            frame_del_e,
            exc_info=True,
        )

    # 2. Delete Raw File
    raw_field = getattr(video, "raw_file", None)
    if raw_field and raw_field.name:
        # Trust storage backend to delete (handles local, S3, Azure automatically)
        delete_field_file(raw_field, missing_ok=True, save=False)
        logger.info(
            "Deleted raw field file via storage backend for %s", video.video_hash
        )
    else:
        # Fallback to local cleanup if FieldFile is corrupted
        raw_file_path = _get_raw_file_path(video)
        if raw_file_path and raw_file_path.exists():
            safe_unlink_file(raw_file_path, missing_ok=True)
            logger.info("Deleted orphaned local raw file for %s", video.video_hash)

    # 3. Delete Processed File
    processed_field = getattr(video, "processed_file", None)
    if processed_field and processed_field.name:
        # Trust storage backend
        delete_field_file(processed_field, missing_ok=True, save=False)
        logger.info(
            "Deleted processed field file via storage backend for %s", video.video_hash
        )
    else:
        # Fallback to local cleanup
        processed_file_path = _get_processed_file_path(video)
        if processed_file_path and processed_file_path.exists():
            safe_unlink_file(processed_file_path, missing_ok=True)
            logger.info(
                "Deleted orphaned local processed file for %s", video.video_hash
            )

    # 4. Delete Database Record
    try:
        super(type(video), video).delete(*args, **kwargs)
        logger.info(
            "Deleted VideoFile database record PK %s (UUID: %s).",
            video.pk,
            video.video_hash,
        )
        return f"Successfully deleted VideoFile {video.video_hash} and attempted file cleanup."
    except Exception as e:
        logger.error(
            "Error deleting VideoFile database record PK %s (UUID: %s): %s",
            video.pk,
            video.video_hash,
            e,
            exc_info=True,
        )
        raise


def _get_base_frame_dir(video: "VideoFile") -> Path:
    return data_paths["frame"] / str(video.video_hash)


def _set_frame_dir(video: "VideoFile", force_update: bool = False):
    target_dir = _get_base_frame_dir(video)
    target_path_str = target_dir.as_posix()

    if not video.frame_dir or video.frame_dir != target_path_str or force_update:
        video.frame_dir = target_path_str
        logger.info(
            "Set frame_dir for video %s to %s", video.video_hash, video.frame_dir
        )
        if not getattr(video, "_saving", False):
            video.save(update_fields=["frame_dir"])


def _get_frame_dir_path(video: "VideoFile") -> Optional[Path]:
    if not video.frame_dir:
        _set_frame_dir(video)
    return Path(video.frame_dir)


def _get_temp_anonymized_frame_dir(video: "VideoFile") -> Path:
    base_frame_dir = _get_base_frame_dir(video)
    anon_dir = base_frame_dir.parent / f"anonymizing_{base_frame_dir.name}"
    return anon_dir


def _get_target_anonymized_video_path(video: "VideoFile") -> Path:
    """Determines the target path for the anonymized/processed video file."""
    if not video.has_raw or not getattr(video.raw_file, "name", None):
        raise ValueError(
            "Cannot determine target anonymized path without a raw file reference."
        )

    # Use the filename part of the raw file's relative path
    raw_path_relative = Path(video.raw_file.name)

    # Use the data_paths dictionary mapping instead of the uppercase constant
    target_dir = data_paths["anonym_video"]

    return target_dir / raw_path_relative.name
