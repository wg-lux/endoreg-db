from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from endoreg_db.utils.filesystem import paths as path_utils
from endoreg_db.utils.filesystem.file_operations import (
    atomic_move_path,
    atomic_write_file,
    ensure_file_mtime_after,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.filesystem.paths import (
    protected_media_root,
    to_protected_media_relative,
    to_storage_relative,
)
from endoreg_db.utils.storage.profile import (
    PayloadKind,
    StoragePolicy,
    resolve_storage_policy,
)
from endoreg_db.utils.storage.streaming import field_file_size, iter_field_file_bytes
from endoreg_db.utils.encryption.encrypted import MAGIC as LX_ENCRYPTED_MAGIC

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile


def _streamable_video_root() -> Path:
    return Path(
        os.environ.get(
            "LX_ANNOTATE_STREAMABLE_VIDEO_ROOT",
            str(protected_media_root() / "streamable_videos"),
        )
    ).resolve()


def _streamable_raw_video_root() -> Path:
    return Path(
        os.environ.get(
            "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT",
            str(_streamable_video_root() / "raw"),
        )
    ).resolve()


def _streamable_processed_video_root() -> Path:
    return Path(
        os.environ.get(
            "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
            str(_streamable_video_root() / "processed"),
        )
    ).resolve()


STREAMABLE_VIDEO_ROOT = _streamable_video_root()
STREAMABLE_RAW_VIDEO_ROOT = _streamable_raw_video_root()
STREAMABLE_PROCESSED_VIDEO_ROOT = _streamable_processed_video_root()
_DEFAULT_STREAMABLE_RAW_VIDEO_ROOT_FN = _streamable_raw_video_root
_DEFAULT_STREAMABLE_PROCESSED_VIDEO_ROOT_FN = _streamable_processed_video_root
STREAMABLE_DIRECTORY_MODE = 0o750
STREAMABLE_FILE_MODE = 0o640


def _streamable_relative_path(target_path: Path) -> str:
    resolved_target = Path(target_path).resolve()

    storage_roots = (
        Path(path_utils.STORAGE_DIR).resolve(),
        path_utils.EndoregPathsModel.from_environment().storage.resolve(),
    )
    for storage_root in dict.fromkeys(storage_roots):
        try:
            return resolved_target.relative_to(storage_root).as_posix()
        except ValueError:
            continue

    try:
        storage_relative = to_storage_relative(resolved_target)
        if not Path(storage_relative).is_absolute():
            return storage_relative
    except ValueError:
        pass

    try:
        return to_protected_media_relative(resolved_target)
    except ValueError as protected_exc:
        raise ValueError(
            f"Could not derive streamable relative path for {resolved_target}"
        ) from protected_exc


def _is_encrypted_file(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(len(LX_ENCRYPTED_MAGIC)) == LX_ENCRYPTED_MAGIC


def _is_sha256_hex(value: str) -> bool:
    value = value.strip()
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _streamable_target_matches_source(
    *,
    target_path: Path,
    video_field_file,
    expected_hash: str,
) -> bool:
    if not target_path.exists() or _is_encrypted_file(target_path):
        return False

    if expected_hash and _is_sha256_hex(expected_hash):
        return sha256_file(target_path) == expected_hash

    try:
        if target_path.stat().st_size != field_file_size(video_field_file):
            return False
    except OSError:
        return False
    return sha256_file(target_path) == sha256_file(video_field_file)


def _materialize_streamable_target(
    video_field_file,
    target_path: Path,
    *,
    expected_hash: str = "",
) -> Path:
    file_size = field_file_size(video_field_file)
    temp_target = target_path.with_name(
        f".{target_path.name}.streamable.{os.getpid()}.{uuid4().hex}.tmp"
    )

    try:
        path = atomic_write_file(
            destination=temp_target,
            content=iter_field_file_bytes(
                video_field_file,
                start=0,
                end=file_size - 1,
            ),
            required_bytes=file_size,
            file_mode=STREAMABLE_FILE_MODE,
            dir_mode=STREAMABLE_DIRECTORY_MODE,
        )

        if _is_encrypted_file(path):
            raise RuntimeError(f"Refusing encrypted streamable artifact: {target_path}")

        if (
            expected_hash
            and _is_sha256_hex(expected_hash)
            and sha256_file(path) != expected_hash
        ):
            raise RuntimeError(
                f"Refusing streamable artifact with unexpected hash: {target_path}"
            )

        return atomic_move_path(
            source=path,
            destination=target_path,
            dir_mode=STREAMABLE_DIRECTORY_MODE,
        )
    except Exception:
        safe_unlink_file(temp_target, missing_ok=True)
        raise


def _configured_streamable_raw_video_root() -> Path:
    if _streamable_raw_video_root is not _DEFAULT_STREAMABLE_RAW_VIDEO_ROOT_FN:
        return Path(_streamable_raw_video_root()).resolve()
    return Path(STREAMABLE_RAW_VIDEO_ROOT).resolve()


def _configured_streamable_processed_video_root() -> Path:
    if (
        _streamable_processed_video_root
        is not _DEFAULT_STREAMABLE_PROCESSED_VIDEO_ROOT_FN
    ):
        return Path(_streamable_processed_video_root()).resolve()
    return Path(STREAMABLE_PROCESSED_VIDEO_ROOT).resolve()


def _video_streamable_target(
    video: "VideoFile", *, processed: bool, suffix: str
) -> Path:
    stem = (
        getattr(video, "processed_video_hash", None) or video.video_hash
        if processed
        else video.video_hash
    )
    root = (
        _configured_streamable_processed_video_root()
        if processed
        else _configured_streamable_raw_video_root()
    )
    return root / f"{stem}{suffix}"


def _sync_one_streamable(
    *,
    video_field_file,
    target_path: Path,
    current_relative_path: str,
    expected_hash: str,
    save: bool,
) -> tuple[str, bool]:
    """
    Returns:
        (relative_path, materialized_or_verified)
    """
    relative_path = _streamable_relative_path(target_path)

    if current_relative_path == relative_path and _streamable_target_matches_source(
        target_path=target_path,
        video_field_file=video_field_file,
        expected_hash=expected_hash,
    ):
        return relative_path, True

    if _streamable_target_matches_source(
        target_path=target_path,
        video_field_file=video_field_file,
        expected_hash=expected_hash,
    ):
        return relative_path, True

    if not save:
        return relative_path, False

    previous_mtime_ns = None
    if target_path.exists():
        previous_mtime_ns = target_path.stat().st_mtime_ns

    materialized_path = _materialize_streamable_target(
        video_field_file,
        target_path,
        expected_hash=expected_hash,
    )
    if previous_mtime_ns is not None:
        ensure_file_mtime_after(
            materialized_path,
            previous_mtime_ns=previous_mtime_ns,
        )
    return _streamable_relative_path(materialized_path), True


def sync_video_streamable_artifacts(
    video: "VideoFile",
    *,
    include_raw: bool = True,
    include_processed: bool = True,
    save: bool = True,
) -> list[str]:
    update_fields: list[str] = []
    raw_storage_policy = resolve_storage_policy(PayloadKind.VIDEO_RAW)
    processed_storage_policy = resolve_storage_policy(PayloadKind.VIDEO_PROCESSED)
    synced_raw = False
    synced_processed = False
    raw_file = getattr(video, "raw_file", None)
    raw_file_name = getattr(raw_file, "name", None)
    if (
        raw_storage_policy == StoragePolicy.FS_STREAMABLE
        and include_raw
        and raw_file
        and isinstance(raw_file_name, str)
        and raw_file_name
    ):
        target_path = _video_streamable_target(
            video,
            processed=False,
            suffix=Path(raw_file_name).suffix or ".mp4",
        )
        relative_path, synced_raw = _sync_one_streamable(
            video_field_file=raw_file,
            target_path=target_path,
            current_relative_path=video.raw_streamable_relative_path,
            expected_hash=(getattr(video, "video_hash", "") or "").strip(),
            save=save,
        )

        if synced_raw and video.raw_streamable_relative_path != relative_path:
            video.raw_streamable_relative_path = relative_path
            update_fields.append("raw_streamable_relative_path")
    elif include_raw and raw_file and isinstance(raw_file_name, str) and raw_file_name:
        logger.info(
            "Skipping raw streamable artifact sync for video %s because "
            "ENDOREG_STORAGE_PROFILE routes raw video to %s.",
            video.pk,
            raw_storage_policy,
        )
        if video.raw_streamable_relative_path:
            video.raw_streamable_relative_path = ""
            update_fields.append("raw_streamable_relative_path")
        synced_raw = False
    processed_file = getattr(video, "processed_file", None)
    processed_file_name = getattr(processed_file, "name", None)
    if (
        processed_storage_policy == StoragePolicy.FS_STREAMABLE
        and include_processed
        and processed_file
        and isinstance(processed_file_name, str)
        and processed_file_name
    ):
        target_path = _video_streamable_target(
            video,
            processed=True,
            suffix=Path(processed_file_name).suffix or ".mp4",
        )
        relative_path, synced_processed = _sync_one_streamable(
            video_field_file=processed_file,
            target_path=target_path,
            current_relative_path=video.processed_streamable_relative_path,
            expected_hash=(getattr(video, "processed_video_hash", "") or "").strip(),
            save=save,
        )

        if (
            synced_processed
            and video.processed_streamable_relative_path != relative_path
        ):
            video.processed_streamable_relative_path = relative_path
            update_fields.append("processed_streamable_relative_path")
    elif (
        include_processed
        and processed_file
        and isinstance(processed_file_name, str)
        and processed_file_name
    ):
        logger.info(
            "Skipping processed streamable artifact sync for video %s because "
            "ENDOREG_STORAGE_PROFILE routes processed video to %s.",
            video.pk,
            processed_storage_policy,
        )
        if video.processed_streamable_relative_path:
            video.processed_streamable_relative_path = ""
            update_fields.append("processed_streamable_relative_path")

        synced_processed = False

    has_verified_streamable = synced_raw or synced_processed

    if not has_verified_streamable:
        if video.raw_streamable_relative_path:
            video.raw_streamable_relative_path = ""
            update_fields.append("raw_streamable_relative_path")
        if video.processed_streamable_relative_path:
            video.processed_streamable_relative_path = ""
            update_fields.append("processed_streamable_relative_path")

    storage_mode_cls = type(video).StorageMode
    preferred_storage_mode = (
        storage_mode_cls.FS_ENCRYPTED_STREAMABLE
        if has_verified_streamable
        else storage_mode_cls.APP_ENCRYPTED
    )

    if video.storage_mode != preferred_storage_mode:
        video.storage_mode = preferred_storage_mode
        update_fields.append("storage_mode")
    if save and update_fields:
        video.save(update_fields=list(dict.fromkeys(update_fields)))
        logger.info(
            "Synchronized streamable video artifacts for %s (%s)",
            video.pk,
            ", ".join(dict.fromkeys(update_fields)),
        )
    return list(dict.fromkeys(update_fields))
