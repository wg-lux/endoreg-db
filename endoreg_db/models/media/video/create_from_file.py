from __future__ import annotations

import logging
import shutil
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type

from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.utils.filesystem.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    ensure_disk_capacity,
    ensure_directory,
)
from endoreg_db.utils.filesystem.paths import (
    IMPORT_VIDEO_DIR,
    SENSITIVE_VIDEO_DIR,
)
from endoreg_db.utils.storage import field_file_is_readable, save_local_file

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

import endoreg_db.utils.filesystem.paths as path_utils

from ....utils.video.ffmpeg_wrapper import transcode_videofile_if_required

logger = logging.getLogger(__name__)

TRANSCODING_DIR = path_utils.data_paths["transcoding"]


def _verify_completed_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Expected output file does not exist: {path}")
    if not path.is_file():
        raise RuntimeError(f"Expected output path is not a file: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Expected output file is empty: {path}")


def _temp_media_path(base_path: Path, marker: str) -> Path:
    """
    Keep the media suffix last so FFmpeg can infer the container.

    Example:
        abc.mp4 -> abc.part.mp4
    """
    return base_path.with_name(f"{base_path.stem}.{marker}{base_path.suffix}")


def check_storage_capacity(
    src_path: Path,
    dst_root: Path,
    safety_margin: float = 1.2,
) -> None:
    src_path = Path(src_path)
    dst_root = Path(dst_root)

    try:
        src_size = src_path.stat().st_size
        ensure_disk_capacity(
            destination_dir=dst_root,
            required_bytes=src_size,
            safety_margin=safety_margin,
        )
        logger.info(
            "Storage check passed for %s into %s with %.2fx safety margin",
            src_path,
            dst_root,
            safety_margin,
        )
    except OSError as exc:
        if "Insufficient disk space" in str(exc):
            free_space = 0
            try:
                free_space = shutil.disk_usage(dst_root).free
            except OSError:
                pass

            raise InsufficientStorageError(
                (
                    "Insufficient storage space. "
                    f"Required: {int(src_size * safety_margin) / 1e9:.1f} GB, "
                    f"Available: {free_space / 1e9:.1f} GB on {dst_root}"
                ),
                required_space=int(src_size * safety_margin),
                available_space=free_space,
            ) from exc

        logger.warning("Could not check storage capacity: %s", exc)


def atomic_copy_with_fallback(
    src_path: Path = IMPORT_VIDEO_DIR,
    dst_path: Path = SENSITIVE_VIDEO_DIR,
) -> bool:
    src_path = Path(src_path)
    dst_path = Path(dst_path)

    try:
        atomic_copy_file(
            source=src_path,
            destination=dst_path,
            preserve_metadata=True,
        )
        logger.debug("Copy successful: %s -> %s", src_path, dst_path)
        return True
    except OSError as exc:
        if "Insufficient disk space" in str(exc):
            free_space = 0
            try:
                free_space = shutil.disk_usage(dst_path.parent).free
            except OSError:
                pass

            required_space = src_path.stat().st_size
            raise InsufficientStorageError(
                (
                    "Insufficient space for copy operation. "
                    f"Required: {required_space / 1e9:.1f} GB, "
                    f"Available: {free_space / 1e9:.1f} GB"
                ),
                required_space=required_space,
                available_space=free_space,
            ) from exc

        logger.error("Copy operation failed: %s -> %s: %s", src_path, dst_path, exc)
        raise


def atomic_move_with_fallback(src_path: Path, dst_path: Path) -> bool:
    src_path = Path(src_path)
    dst_path = Path(dst_path)

    try:
        atomic_move_file(source=src_path, destination=dst_path)
        logger.debug("Atomic move successful: %s -> %s", src_path, dst_path)
        return True
    except OSError as exc:
        if "Insufficient disk space" in str(exc):
            free_space = 0
            try:
                free_space = shutil.disk_usage(dst_path.parent).free
            except OSError:
                pass

            required_space = src_path.stat().st_size
            raise InsufficientStorageError(
                (
                    "Insufficient space for move operation. "
                    f"Required: {required_space / 1e9:.1f} GB, "
                    f"Available: {free_space / 1e9:.1f} GB"
                ),
                required_space=required_space,
                available_space=free_space,
            ) from exc

        logger.error("Failed to move %s -> %s: %s", src_path, dst_path, exc)
        raise


def _get_data_paths():
    """Return current data_paths mapping, including patched instances in tests."""
    utils_module = import_module("endoreg_db.utils")
    return getattr(utils_module, "data_paths")


def _get_path(mapping, key, default):
    """Access mapping by key using __getitem__ so MagicMocks with side effects work."""
    if mapping is None:
        return default
    try:
        return mapping[key]
    except (KeyError, TypeError):
        return default


def _safe_unlink_local(path: Path | None, *, label: str) -> None:
    """
    Delete only local staging paths. Never pass FieldFile-backed canonical storage here.
    """
    safe_cleanup_staging_file(path, label=label, missing_ok=True)


def _create_from_file(
    cls_model: Type["VideoFile"],
    file_path: Path,
    center_name: str,
    processor_name: Optional[str],
    video_hash: str,
    video_dir: Path = IMPORT_VIDEO_DIR,
    save: bool = True,
    **kwargs,
) -> "VideoFile":
    """
    Create a VideoFile from a local source path.

    Storage model:

    - file_path: local import/staging input
    - temp_output_path: local plaintext standardized/transcoded staging output
    - video.raw_file: canonical storage-backed FieldFile, possibly encrypted
    - storage_name: logical FieldFile name, not necessarily a direct filesystem path
    """
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.medical.hardware import EndoscopyProcessor

    file_path = Path(file_path)

    original_file_name = file_path.name
    original_suffix = file_path.suffix

    temp_output_path: Path | None = None
    transcoded_file_path: Path | None = None
    canonical_source_path: Path | None = None

    try:
        data_paths = _get_data_paths()

        resolved_video_dir = _get_path(data_paths, "sensitive_video", video_dir)
        staging_video_dir = Path(resolved_video_dir)
        ensure_directory(staging_video_dir)

        storage_root_default = staging_video_dir.parent
        resolved_storage_root = _get_path(data_paths, "storage", storage_root_default)
        storage_root = Path(resolved_storage_root)
        ensure_directory(storage_root)

        check_storage_capacity(file_path, storage_root)

        storage_name = f"{video_hash}{original_suffix}"

        # This is a local staging path only. It is not the canonical final storage path.
        temp_output_path = _temp_media_path(staging_video_dir / storage_name, "part")
        ensure_directory(temp_output_path.parent)

        _safe_unlink_local(temp_output_path, label="stale temp output")

        logger.debug("Checking transcoding requirement for %s", file_path)

        try:
            transcoded_file_path = transcode_videofile_if_required(
                input_path=file_path,
                output_path=temp_output_path,
            )
        except Exception as exc:
            raise RuntimeError(
                "Video standardization failed; refusing to promote the original file "
                f"into canonical raw storage for {file_path}."
            ) from exc

        if transcoded_file_path is None:
            raise RuntimeError(
                "Video standardization did not produce a compliant output; refusing "
                f"to promote the original file into canonical raw storage for {file_path}."
            )

        transcoded_file_path = Path(transcoded_file_path)
        logger.debug("Standardized video candidate: %s", transcoded_file_path)

        existing_video = cls_model.objects.filter(video_hash=video_hash).first()
        if existing_video is not None:
            logger.warning(
                "Video with hash %s already exists; checking canonical raw_file readability.",
                video_hash,
            )

            if field_file_is_readable(existing_video.raw_file):
                logger.warning(
                    "Video with hash %s already exists and raw_file is readable. "
                    "Returning existing instance.",
                    video_hash,
                )

                if transcoded_file_path != file_path:
                    _safe_unlink_local(
                        transcoded_file_path, label="duplicate transcoded file"
                    )

                if temp_output_path != transcoded_file_path:
                    _safe_unlink_local(temp_output_path, label="duplicate temp output")

                return existing_video

            logger.warning(
                "Video with hash %s exists but raw_file is missing/unreadable. "
                "Deleting orphaned record.",
                video_hash,
            )
            existing_video.delete()

        try:
            if transcoded_file_path == temp_output_path:
                _verify_completed_file(temp_output_path)
                canonical_source_path = temp_output_path
            else:
                logger.debug(
                    "Copying standardized file %s to local staging destination %s",
                    transcoded_file_path,
                    temp_output_path,
                )
                atomic_copy_with_fallback(transcoded_file_path, temp_output_path)
                _verify_completed_file(temp_output_path)
                canonical_source_path = temp_output_path
        except InsufficientStorageError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Failed to prepare standardized file for storage: {exc}"
            ) from exc

        try:
            center = Center.objects.get(name=center_name)
            processor = (
                EndoscopyProcessor.objects.get(name=processor_name)
                if processor_name
                else None
            )

            logger.debug(
                "Found Center: %s, Processor: %s",
                center.name,
                processor.name if processor else "None",
            )
        except Center.DoesNotExist as exc:
            raise ValueError(f"Center '{center_name}' not found.") from exc
        except EndoscopyProcessor.DoesNotExist as exc:
            raise ValueError(f"Processor '{processor_name}' not found.") from exc

        logger.info("Creating new VideoFile instance with hash: %s", video_hash)

        video = cls_model(
            processed_file=None,
            center=center,
            processor=processor,
            original_file_name=original_file_name,
            video_hash=video_hash,
            processed_video_hash=None,
            suffix=original_suffix,
            fps=None,
            **kwargs,
        )

        _verify_completed_file(canonical_source_path)

        save_local_file(
            video.raw_file,
            canonical_source_path,
            name=storage_name,
            save=False,
        )

        # Validate through storage after save_local_file. This catches broken encryption/save.
        if not field_file_is_readable(video.raw_file):
            raise RuntimeError(
                f"Stored raw_file for video hash {video_hash} is not readable after save."
            )

        _safe_unlink_local(canonical_source_path, label="canonical source staging file")

        if transcoded_file_path is not None and transcoded_file_path not in {
            file_path,
            canonical_source_path,
        }:
            _safe_unlink_local(transcoded_file_path, label="transcoded staging file")

        if save:
            logger.info("Saving new VideoFile instance with hash %s", video_hash)
            video.save()
            logger.info("Successfully created VideoFile PK %s", video.pk)

        return video

    except (InsufficientStorageError, ValueError):
        _safe_unlink_local(temp_output_path, label="temp output after failure")

        if transcoded_file_path is not None and transcoded_file_path not in {
            file_path,
            temp_output_path,
        }:
            _safe_unlink_local(
                transcoded_file_path, label="transcoded file after failure"
            )

        raise

    except Exception as exc:
        logger.error(
            "Failed to create VideoFile from %s: (%s) %s",
            file_path,
            type(exc).__name__,
            exc,
            exc_info=True,
        )

        _safe_unlink_local(temp_output_path, label="temp output after failure")

        if transcoded_file_path is not None and transcoded_file_path not in {
            file_path,
            temp_output_path,
        }:
            _safe_unlink_local(
                transcoded_file_path, label="transcoded file after failure"
            )

        raise RuntimeError(f"Video processing failed: {exc}") from exc
