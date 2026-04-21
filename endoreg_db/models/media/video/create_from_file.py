import logging
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type

# Import the new exceptions from the correct path
from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    atomic_move_file,
    ensure_disk_capacity,
)
from endoreg_db.utils.paths import (
    IMPORT_VIDEO_DIR,
    SENSITIVE_VIDEO_DIR,
)

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

import endoreg_db.utils.paths as path_utils

from ....utils.video.ffmpeg_wrapper import transcode_videofile_if_required

logger = logging.getLogger(__name__)
TRANSCODING_DIR = path_utils.data_paths["transcoding"]


def _verify_completed_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Expected output file does not exist: {path}")
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Expected output file is empty: {path}")


def _promote_atomic(temp_path: Path, final_path: Path) -> None:
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    if not temp_path.exists():
        if final_path.exists():
            logger.debug(
                "Temp file missing, but final file exists. Assuming atomic move already occurred."
            )
            return
        else:
            # If neither exists, then we genuinely have a failure.
            raise RuntimeError(
                f"Expected output file does not exist at {temp_path} and was not found at {final_path}"
            )

    _verify_completed_file(temp_path)
    atomic_move_file(source=temp_path, destination=final_path)
    logger.debug("Promoted file atomically: %s -> %s", temp_path, final_path)


def _temp_media_path(final_path: Path, marker: str) -> Path:
    """Keep the media suffix last so FFmpeg can infer the container."""
    return final_path.with_name(f"{final_path.stem}.{marker}{final_path.suffix}")


def check_storage_capacity(
    src_path: Path, dst_root: Path, safety_margin: float = 1.2
) -> None:
    """
    Check if there's enough storage space before starting operations.

    Args:
        src_path: Source file path
        dst_root: Destination root directory
        safety_margin: Safety factor (1.2 = 20% extra space required)

    Raises:
        InsufficientStorageError: If insufficient storage space
    """
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

    except OSError as e:
        if "Insufficient disk space" in str(e):
            free_space = 0
            try:
                import shutil

                free_space = shutil.disk_usage(dst_root).free
            except OSError:
                pass
            raise InsufficientStorageError(
                f"Insufficient storage space. Required: {int(src_size * safety_margin) / 1e9:.1f} GB, Available: {free_space / 1e9:.1f} GB on {dst_root}",
                required_space=int(src_size * safety_margin),
                available_space=free_space,
            ) from e
        logger.warning(f"Could not check storage capacity: {e}")
        # Don't fail the operation, just log the warning


def atomic_copy_with_fallback(
    src_path: Path = IMPORT_VIDEO_DIR, dst_path: Path = SENSITIVE_VIDEO_DIR
) -> bool:
    """
    Atomically copy file from src to dst, preserving the source file.

    Args:
        src_path: Source file path
        dst_path: Destination file path

    Returns:
        True if successful

    Raises:
        InsufficientStorageError: If not enough space for the operation
        OSError: For other file system errors
    """
    try:
        atomic_copy_file(source=src_path, destination=dst_path, preserve_metadata=True)
        logger.debug(f"Copy successful: {src_path} -> {dst_path}")
        return True
    except OSError as e:
        if "Insufficient disk space" in str(e):
            free_space = 0
            try:
                import shutil

                free_space = shutil.disk_usage(dst_path.parent).free
            except OSError:
                pass
            raise InsufficientStorageError(
                f"Insufficient space for copy operation. Required: {src_path.stat().st_size / 1e9:.1f} GB, Available: {free_space / 1e9:.1f} GB",
                required_space=src_path.stat().st_size,
                available_space=free_space,
            ) from e
        logger.error(f"Copy operation failed: {src_path} -> {dst_path}: {e}")
        raise
    except Exception as e:
        logger.error(f"Copy operation failed: {src_path} -> {dst_path}: {e}")
        raise


def atomic_move_with_fallback(src_path: Path, dst_path: Path) -> bool:
    """
    Atomically move file from src to dst, with fallback to copy+remove.

    Args:
        src_path: Source file path
        dst_path: Destination file path

    Returns:
        True if successful

    Raises:
        InsufficientStorageError: If not enough space for the operation
        OSError: For other file system errors
    """
    try:
        atomic_move_file(source=src_path, destination=dst_path)
        logger.debug(f"Atomic move successful: {src_path} -> {dst_path}")
        return True
    except OSError as e:
        if "Insufficient disk space" in str(e):
            free_space = 0
            try:
                import shutil

                free_space = shutil.disk_usage(dst_path.parent).free
            except OSError:
                pass
            raise InsufficientStorageError(
                f"Insufficient space for copy operation. Required: {src_path.stat().st_size / 1e9:.1f} GB, Available: {free_space / 1e9:.1f} GB",
                required_space=src_path.stat().st_size,
                available_space=free_space,
            ) from e
        logger.error(f"Failed to move {src_path} -> {dst_path}: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to move {src_path} -> {dst_path}: {e}")
        raise


def _get_data_paths():
    """Return the current data_paths mapping (supports patched instances in tests)."""
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
    Creates a VideoFile instance from a given video file path with improved error handling.

    Raises:
        InsufficientStorageError: When not enough disk space
        ValueError: When required objects (Center, Processor) not found
        RuntimeError: For other processing errors
    """
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.medical.hardware import EndoscopyProcessor

    original_file_name = file_path.name
    original_suffix = file_path.suffix
    final_storage_path = None
    transcoded_file_path = None
    temp_output_path = None

    try:
        # Ensure we operate under the canonical video path root
        data_paths = _get_data_paths()
        resolved_video_dir = _get_path(data_paths, "sensitive_video", video_dir)
        video_dir = Path(resolved_video_dir)
        storage_root_default = Path(video_dir).parent
        resolved_storage_root = _get_path(data_paths, "storage", storage_root_default)
        storage_root = Path(resolved_storage_root)
        storage_root.mkdir(parents=True, exist_ok=True)

        # Check storage capacity before starting any work
        check_storage_capacity(file_path, storage_root)

        filename = f"{video_hash}{original_suffix}"
        final_storage_path = video_dir / filename
        temp_output_path = _temp_media_path(final_storage_path, "part")

        # Ensure the DIRECTORY exists (video_dir), not the parent of a nested file
        final_storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output_path.parent.mkdir(parents=True, exist_ok=True)

        if temp_output_path.exists():
            temp_output_path.unlink(missing_ok=True)

        # 1. Transcode if necessary
        logger.debug("Checking transcoding requirement for %s", file_path)

        try:
            transcoded_file_path = transcode_videofile_if_required(
                input_path=file_path, output_path=temp_output_path
            )
        except Exception as e:
            raise RuntimeError(
                "Video standardization failed; refusing to promote the original file "
                f"into canonical raw storage for {file_path}."
            ) from e

        if transcoded_file_path is None:
            raise RuntimeError(
                "Video standardization did not produce a compliant output; refusing "
                f"to promote the original file into canonical raw storage for {file_path}."
            )

        logger.debug("Using file for hashing: %s", transcoded_file_path)

        # 3. Check if hash already exists (Fixed TOCTOU Race Condition)
        existing_video = cls_model.objects.filter(video_hash=video_hash).first()
        if existing_video:
            logger.warning(
                "Video with hash %s already exists (UUID: %s)",
                video_hash,
                existing_video.video_hash,
            )

            # Check if the existing video has a valid file
            existing_raw_path = existing_video.get_raw_file_path()
            if (
                existing_video.has_raw
                and existing_raw_path
                and existing_raw_path.exists()
            ):
                logger.warning(
                    "Video with hash %s already exists and file is present. Returning existing instance.",
                    video_hash,
                )
                # Clean up transcoded file if it was created temporarily
                if transcoded_file_path != file_path and transcoded_file_path.exists():
                    transcoded_file_path.unlink(missing_ok=True)
                return existing_video

            logger.warning(
                "Video with hash %s exists but file is missing. Deleting orphaned record.",
                video_hash,
            )
            existing_video.delete()

        # 5. Move or Copy the file to final storage using improved method
        try:
            if transcoded_file_path == temp_output_path:
                _promote_atomic(temp_output_path, final_storage_path)
            else:
                logger.debug(
                    "Copying file %s to temporary destination %s",
                    transcoded_file_path,
                    temp_output_path,
                )
                atomic_copy_with_fallback(transcoded_file_path, temp_output_path)
                _promote_atomic(temp_output_path, final_storage_path)
        except InsufficientStorageError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to move file to final storage: {e}") from e

        # 7. Get related objects
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
        except Center.DoesNotExist as e:
            logger.error("Center '%s' not found", center_name)
            if final_storage_path and final_storage_path.exists():
                final_storage_path.unlink(missing_ok=True)
            raise ValueError(f"Center '{center_name}' not found.") from e
        except EndoscopyProcessor.DoesNotExist as e:
            logger.error("Processor '%s' not found", processor_name)
            if final_storage_path and final_storage_path.exists():
                final_storage_path.unlink(missing_ok=True)
            raise ValueError(f"Processor '{processor_name}' not found.") from e

        # 8. Create the VideoFile instance
        logger.info("Creating new VideoFile instance with hash: %s", video_hash)

        relative_name = path_utils.to_storage_relative(final_storage_path)

        # Unpacked **kwargs so any extra fields passed in actually hit the DB
        video = cls_model(
            raw_file=relative_name,
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

        # 9. Save the instance if requested
        if save:
            logger.info("Saving new VideoFile instance (Hash:%s)", video_hash)
            video.save()
            logger.info(
                "Successfully created VideoFile PK %s",
                video.pk,
            )

        return video

    except (InsufficientStorageError, ValueError):
        raise
    except Exception as e:
        logger.error(
            "Failed to create VideoFile from %s: (%s) %s",
            file_path,
            type(e).__name__,
            e,
            exc_info=True,
        )
        # Clean up any created files
        if final_storage_path and final_storage_path.exists():
            logger.warning("Cleaning up orphaned file: %s", final_storage_path)
            final_storage_path.unlink(missing_ok=True)
        if temp_output_path and temp_output_path.exists():
            logger.warning(
                "Cleaning up orphaned temporary output file: %s", temp_output_path
            )
            temp_output_path.unlink(missing_ok=True)
        if (
            transcoded_file_path
            and transcoded_file_path not in {file_path, temp_output_path}
            and transcoded_file_path.exists()
        ):
            logger.warning(
                "Cleaning up orphaned transcoded file: %s", transcoded_file_path
            )
            transcoded_file_path.unlink(missing_ok=True)
        raise RuntimeError(f"Video processing failed: {e}") from e
