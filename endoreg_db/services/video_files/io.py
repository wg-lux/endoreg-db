# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from __future__ import annotations

from endoreg_db.utils.storage.files import canonical_media_name

import logging
from contextlib import ExitStack, contextmanager
from pathlib import Path
from collections.abc import Generator
from typing import TYPE_CHECKING, Optional

from django.db import transaction

from endoreg_db.utils.paths import (
    get_runtime_paths,
    resolve_existing_protected_media_path,
)
from endoreg_db.utils.file_operations import safe_unlink_file
from endoreg_db.utils.rust_backend import is_lx_encrypted_file
from endoreg_db.utils.storage import delete_field_file

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger("video_file")


def streamable_path_is_safe_plaintext(path: Path) -> bool:
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
        starts_with_magic = is_lx_encrypted_file(path)
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


def resolve_streamable_path(relative_path: str | None) -> Optional[Path]:
    if not relative_path:
        return None

    candidate = resolve_existing_protected_media_path(relative_path)
    if candidate is None:
        return None

    return candidate if streamable_path_is_safe_plaintext(candidate) else None


def get_raw_video_file_path(video: "VideoFile") -> Optional[Path]:
    return video.raw_file.local_plaintext_path() if video.has_raw else None


@contextmanager
def ensure_local_raw_video_file(video: "VideoFile") -> Generator[Path]:
    if not video.has_raw:
        raise ValueError(f"Video {video.raw_video_hash} has no raw file")
    with video.raw_file.ensure_local() as local_path:
        yield local_path


def get_raw_video_stream_path(video: "VideoFile") -> Optional[Path]:
    return resolve_streamable_path(getattr(video, "raw_streamable_relative_path", ""))


def get_processed_video_file_path(video: "VideoFile") -> Optional[Path]:
    if not video.is_processed:
        return None
    return (
        get_processed_video_stream_path(video, materialize_if_missing=True)
        or video.processed_file.local_plaintext_path()
    )


@contextmanager
def ensure_local_processed_video_file(video: "VideoFile") -> Generator[Path]:
    """
    Yield a real local plaintext path for external tools.
    """
    if not video.is_processed:
        raise ValueError(f"Video {video.raw_video_hash} has no processed file")

    local_context = video.processed_file.ensure_local()
    with ExitStack() as stack:
        try:
            local_path = stack.enter_context(local_context)
        except (FileNotFoundError, IOError):
            from endoreg_db.services.hub.remote_processed_media import (
                materialize_remote_processed_video,
            )

            remote_path = stack.enter_context(
                materialize_remote_processed_video(video_id=int(video.pk))
            )
            yield Path(remote_path)
            return
        yield Path(local_path)


def get_processed_video_stream_path(
    video: "VideoFile", *, materialize_if_missing: bool = False
) -> Optional[Path]:
    path = resolve_streamable_path(
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
        return resolve_streamable_path(
            getattr(video, "processed_streamable_relative_path", "")
        )

    return None


def delete_raw_file_after_validation(video: "VideoFile") -> bool:
    """
    Delete the canonical raw video after validation.

    Important: delete through storage, not via guessed paths.
    Streamable derived raw copy is cleaned separately.
    """
    from endoreg_db.services.hls_media import delete_video_hls_artifacts

    deleted = delete_video_hls_artifacts(video, artifact_kind="raw")
    raw_field = getattr(video, "raw_file", None)

    if raw_field and raw_field.name:
        deleted = (
            delete_field_file(video, "raw_file", missing_ok=True, save=True) or deleted
        )
    else:
        raw_path = get_raw_video_file_path(video)
        if raw_path is not None and raw_path.exists():
            safe_unlink_file(raw_path, missing_ok=True)
            deleted = True

    raw_stream_path = get_raw_video_stream_path(video)
    if raw_stream_path and raw_stream_path.exists():
        safe_unlink_file(raw_stream_path, missing_ok=True)
        deleted = True

    if getattr(video, "raw_streamable_relative_path", ""):
        video.raw_streamable_relative_path = ""
        save = getattr(video, "save", None)
        if callable(save):
            save(update_fields=["raw_streamable_relative_path"])

    return deleted


@transaction.atomic
def delete_video_with_owned_files(
    video: "VideoFile",
    using: str | None = None,
    keep_parents: bool = False,
) -> tuple[int, dict[str, int]]:
    """
    Delete VideoFile and owned artifacts.

    Canonical raw/processed files are deleted through Django storage.
    Streamable/frame artifacts are path-based derived files and may be unlinked.
    """
    try:
        frame_delete_msg = video.delete_frames()
        logger.info(
            "Frame deletion result for video %s: %s",
            video.raw_video_hash,
            frame_delete_msg,
        )
    except Exception as exc:
        logger.error(
            "Error during frame deletion for video %s: %s",
            video.raw_video_hash,
            exc,
            exc_info=True,
        )

    raw_field = getattr(video, "raw_file", None)
    if raw_field and raw_field.name:
        delete_field_file(raw_field, missing_ok=True, save=False)
        logger.info("Deleted raw file via storage for %s", video.raw_video_hash)

    processed_field = getattr(video, "processed_file", None)
    if processed_field and processed_field.name:
        delete_field_file(processed_field, missing_ok=True, save=False)
        logger.info("Deleted processed file via storage for %s", video.raw_video_hash)

    raw_stream_path = get_raw_video_stream_path(video)
    if raw_stream_path and raw_stream_path.exists():
        safe_unlink_file(raw_stream_path, missing_ok=True)

    processed_stream_path = get_processed_video_stream_path(video)
    if processed_stream_path and processed_stream_path.exists():
        safe_unlink_file(processed_stream_path, missing_ok=True)

    deleted = super(type(video), video).delete(
        using=using,
        keep_parents=keep_parents,
    )

    logger.info(
        "Deleted VideoFile database record PK %s UUID %s.",
        video.pk,
        video.raw_video_hash,
    )
    return deleted


def get_video_base_frame_dir(video: "VideoFile") -> Path:
    return get_runtime_paths().frame


def set_video_frame_dir(video: "VideoFile") -> Path:
    return get_runtime_paths().frame / str(video.raw_video_hash)


def get_video_frame_dir_path(video: "VideoFile") -> Optional[Path]:
    return get_runtime_paths().frame / str(video.raw_video_hash)


def get_temp_anonymized_video_frame_dir(video: "VideoFile") -> Path:
    return get_runtime_paths().transcoding / f"anonymizing_{video.raw_video_hash}"


def get_target_anonymized_video_path(video: "VideoFile") -> Path:
    """
    Return temporary/derived processed-output path.

    This is okay as Path-based because it is not the canonical FileField write.
    Final canonical persistence must still use processed_file.save(...).
    """
    if not video.raw_video_hash:
        raise ValueError("Cannot determine anonymized path without raw_video_hash")

    target_dir = get_runtime_paths().anonym_video
    return target_dir / canonical_media_name(video.raw_video_hash, ".mp4")
