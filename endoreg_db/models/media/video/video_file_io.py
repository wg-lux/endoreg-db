from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from django.db import transaction

from endoreg_db.utils.filesystem import paths as path_utils
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC
from endoreg_db.utils.filesystem.file_operations import safe_unlink_file
from endoreg_db.utils.storage import delete_field_file, ensure_local_file, file_exists
from endoreg_db.utils.storage.streaming import maybe_local_plaintext_path

if TYPE_CHECKING:
    from .video_file import VideoFile

logger = logging.getLogger("video_file")


def _streamable_path_is_safe_plaintext(path: Path) -> bool:
    try:
        stat_result = path.stat()
    except OSError as exc:
        logger.warning(
            "Refusing streamable video artifact that cannot be stated: path=%s error=%s",
            path,
            exc,
        )
        return False

    if not path.is_file() or stat_result.st_size <= 0:
        logger.warning(
            "Refusing invalid streamable video artifact: path=%s size=%s",
            path,
            stat_result.st_size,
        )
        return False

    try:
        with path.open("rb") as handle:
            starts_with_magic = (
                handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC
            )
    except OSError as exc:
        logger.warning(
            "Refusing unreadable streamable video artifact: path=%s error=%s",
            path,
            exc,
        )
        return False

    if starts_with_magic:
        logger.error(
            "Refusing encrypted streamable video artifact: path=%s",
            path,
        )
        return False

    return True


def _resolve_streamable_path(relative_path: str | None) -> Optional[Path]:
    if not relative_path:
        return None

    candidate = path_utils.resolve_existing_protected_media_path(relative_path)
    if candidate is None:
        return None

    return candidate if _streamable_path_is_safe_plaintext(candidate) else None


def _field_file_exists(field_file) -> bool:
    return bool(
        field_file and getattr(field_file, "name", None) and file_exists(field_file)
    )


def _get_raw_file_path(video: "VideoFile") -> Optional[Path]:
    """
    Deprecated/best-effort local-path accessor.

    Do not use this for canonical encrypted raw files.
    Use _ensure_local_raw_file() when a real local plaintext file is required.
    """
    raw_field = getattr(video, "raw_file", None)
    if not (video.has_raw and raw_field and raw_field.name):
        return None

    return maybe_local_plaintext_path(raw_field)


@contextmanager
def _ensure_local_raw_file(video: "VideoFile") -> Iterator[Path]:
    """
    Yield a real local plaintext path for external tools.

    This is the correct boundary for FFmpeg/cv2/etc.
    """
    raw_field = getattr(video, "raw_file", None)
    if not (video.has_raw and raw_field and raw_field.name):
        raise ValueError(f"Video {video.video_hash} has no raw file")

    with ensure_local_file(raw_field) as local_path:
        yield Path(local_path)


def _get_raw_stream_path(video: "VideoFile") -> Optional[Path]:
    return _resolve_streamable_path(getattr(video, "raw_streamable_relative_path", ""))


def _get_processed_file_path(video: "VideoFile") -> Optional[Path]:
    """
    Deprecated/best-effort local-path accessor.

    Do not use this for canonical encrypted processed files.
    Use _ensure_local_processed_file() when a real local plaintext file is required.
    """
    processed_field = getattr(video, "processed_file", None)
    if not (video.is_processed and processed_field and processed_field.name):
        return None

    if hasattr(video, "processed_streamable_relative_path"):
        try:
            stream_path = _get_processed_stream_path(
                video,
                materialize_if_missing=True,
            )
        except AttributeError as exc:
            logger.debug(
                "Could not materialize processed stream path for %s: %s",
                getattr(video, "video_hash", "<unknown>"),
                exc,
            )
        else:
            if stream_path is not None:
                return stream_path

    return maybe_local_plaintext_path(processed_field)


@contextmanager
def _ensure_local_processed_file(video: "VideoFile") -> Iterator[Path]:
    """
    Yield a real local plaintext path for external tools.
    """
    processed_field = getattr(video, "processed_file", None)
    if not (video.is_processed and processed_field and processed_field.name):
        raise ValueError(f"Video {video.video_hash} has no processed file")

    with ensure_local_file(processed_field) as local_path:
        yield Path(local_path)


def _get_processed_stream_path(
    video: "VideoFile", *, materialize_if_missing: bool = False
) -> Optional[Path]:
    path = _resolve_streamable_path(
        getattr(video, "processed_streamable_relative_path", "")
    )
    if path is not None:
        return path

    if materialize_if_missing:
        from endoreg_db.services.streamable_media import sync_video_streamable_artifacts

        sync_video_streamable_artifacts(
            video,
            include_raw=False,
            include_processed=True,
            save=True,
        )
        return _resolve_streamable_path(
            getattr(video, "processed_streamable_relative_path", "")
        )

    return None


def _delete_raw_file_after_validation(video: "VideoFile") -> bool:
    """
    Delete the canonical raw video after validation.

    Important: delete through storage, not via guessed paths.
    Streamable derived raw copy is cleaned separately.
    """
    raw_field = getattr(video, "raw_file", None)
    deleted = False

    if raw_field and raw_field.name:
        deleted = delete_field_file(video, "raw_file", missing_ok=True, save=True)
    else:
        raw_path = _get_raw_file_path(video)
        if raw_path is not None and raw_path.exists():
            safe_unlink_file(raw_path, missing_ok=True)
            deleted = True

    raw_stream_path = _get_raw_stream_path(video)
    if raw_stream_path and raw_stream_path.exists():
        safe_unlink_file(raw_stream_path, missing_ok=True)

    if getattr(video, "raw_streamable_relative_path", ""):
        video.raw_streamable_relative_path = ""
        save = getattr(video, "save", None)
        if callable(save):
            save(update_fields=["raw_streamable_relative_path"])

    return deleted


@transaction.atomic
def _delete_with_file(video: "VideoFile", *args, **kwargs):
    """
    Delete VideoFile and owned artifacts.

    Canonical raw/processed files are deleted through Django storage.
    Streamable/frame artifacts are path-based derived files and may be unlinked.
    """
    try:
        frame_delete_msg = video.delete_frames()
        logger.info(
            "Frame deletion result for video %s: %s",
            video.video_hash,
            frame_delete_msg,
        )
    except Exception as exc:
        logger.error(
            "Error during frame deletion for video %s: %s",
            video.video_hash,
            exc,
            exc_info=True,
        )

    raw_field = getattr(video, "raw_file", None)
    if raw_field and raw_field.name:
        delete_field_file(raw_field, missing_ok=True, save=False)
        logger.info("Deleted raw file via storage for %s", video.video_hash)

    processed_field = getattr(video, "processed_file", None)
    if processed_field and processed_field.name:
        delete_field_file(processed_field, missing_ok=True, save=False)
        logger.info("Deleted processed file via storage for %s", video.video_hash)

    raw_stream_path = _get_raw_stream_path(video)
    if raw_stream_path and raw_stream_path.exists():
        safe_unlink_file(raw_stream_path, missing_ok=True)

    processed_stream_path = _get_processed_stream_path(video)
    if processed_stream_path and processed_stream_path.exists():
        safe_unlink_file(processed_stream_path, missing_ok=True)

    super(type(video), video).delete(*args, **kwargs)

    logger.info(
        "Deleted VideoFile database record PK %s UUID %s.",
        video.pk,
        video.video_hash,
    )
    return (
        f"Successfully deleted VideoFile {video.video_hash} "
        "and attempted owned artifact cleanup."
    )


def _get_base_frame_dir(video: "VideoFile") -> Path:
    return path_utils.EndoregPathsModel.from_environment().frame / str(video.video_hash)


def _set_frame_dir(video: "VideoFile", force_update: bool = False):
    target_dir = _get_base_frame_dir(video)
    target_path_str = target_dir.as_posix()

    if not video.frame_dir or video.frame_dir != target_path_str or force_update:
        video.frame_dir = target_path_str
        logger.info(
            "Set frame_dir for video %s to %s",
            video.video_hash,
            video.frame_dir,
        )
        if not getattr(video, "_saving", False):
            video.save(update_fields=["frame_dir"])


def _get_frame_dir_path(video: "VideoFile") -> Optional[Path]:
    if not video.frame_dir:
        _set_frame_dir(video)
    return Path(video.frame_dir)


def _get_temp_anonymized_frame_dir(video: "VideoFile") -> Path:
    base_frame_dir = _get_base_frame_dir(video)
    return base_frame_dir.parent / f"anonymizing_{base_frame_dir.name}"


def _get_target_anonymized_video_path(video: "VideoFile") -> Path:
    """
    Return temporary/derived processed-output path.

    This is okay as Path-based because it is not the canonical FileField write.
    Final canonical persistence must still use processed_file.save(...).
    """
    if not video.video_hash:
        raise ValueError("Cannot determine anonymized path without video_hash")

    target_dir = path_utils.EndoregPathsModel.from_environment().anonym_video
    return target_dir / f"{video.video_hash}.mp4"
