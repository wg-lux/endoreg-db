import logging
import shutil
import uuid
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type

# Import the new exceptions from the correct path
from endoreg_db.exceptions import InsufficientStorageError, TranscodingError

from ...utils import TMP_VIDEO_DIR, VIDEO_DIR

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

from ....utils.file_operations import get_uuid_filename
from ....utils.hashs import get_video_hash
from ....utils.video.ffmpeg_wrapper import transcode_videofile_if_required

logger = logging.getLogger(__name__)


def check_storage_capacity(src_path: Path, dst_root: Path, safety_margin: float = 1.2) -> None:
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
        required_space = int(src_size * safety_margin)

        # Check free space on destination
        free_space = shutil.disk_usage(dst_root).free

        if free_space < required_space:
            raise InsufficientStorageError(
                f"Insufficient storage space. Required: {required_space / 1e9:.1f} GB, Available: {free_space / 1e9:.1f} GB on {dst_root}",
                required_space=required_space,
                available_space=free_space,
            )

        logger.info(f"Storage check passed: {free_space / 1e9:.1f} GB available, {required_space / 1e9:.1f} GB required")

    except OSError as e:
        logger.warning(f"Could not check storage capacity: {e}")
        # Don't fail the operation, just log the warning


def atomic_copy_with_fallback(src_path: Path, dst_path: Path) -> bool:
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
        # Check space before copy
        src_size = src_path.stat().st_size
        free_space = shutil.disk_usage(dst_path.parent).free

        if free_space < src_size * 1.1:  # 10% safety margin
            raise InsufficientStorageError(
                f"Insufficient space for copy operation. Required: {src_size / 1e9:.1f} GB, Available: {free_space / 1e9:.1f} GB",
                required_space=src_size,
                available_space=free_space,
            )

        # Use a temporary name during copy for atomicity
        temp_dst = dst_path.with_suffix(dst_path.suffix + ".tmp")

        try:
            shutil.copy2(str(src_path), str(temp_dst))
            temp_dst.rename(dst_path)
            logger.debug(f"Copy successful: {src_path} -> {dst_path}")
            return True
        except Exception:
            # Clean up temp file if copy failed
            if temp_dst.exists():
                temp_dst.unlink(missing_ok=True)
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
        # First try atomic move (same filesystem)
        try:
            src_path.rename(dst_path)
            logger.debug(f"Atomic move successful: {src_path} -> {dst_path}")
            return True
        except OSError as e:
            if e.errno == 18:  # Cross-device link
                logger.debug("Cross-device move detected, falling back to copy+remove")
            else:
                raise

        # Check space before cross-filesystem copy
        src_size = src_path.stat().st_size
        free_space = shutil.disk_usage(dst_path.parent).free

        if free_space < src_size * 1.1:  # 10% safety margin
            raise InsufficientStorageError(
                f"Insufficient space for copy operation. Required: {src_size / 1e9:.1f} GB, Available: {free_space / 1e9:.1f} GB",
                required_space=src_size,
                available_space=free_space,
            )

        # Fallback to copy+remove for cross-filesystem moves
        logger.info(f"Copying file (cross-filesystem): {src_path} -> {dst_path}")

        # Use a temporary name during copy for atomicity
        temp_dst = dst_path.with_suffix(dst_path.suffix + ".tmp")

        try:
            shutil.copy2(str(src_path), str(temp_dst))
            temp_dst.rename(dst_path)
            src_path.unlink()  # Remove source only after successful copy
            logger.debug(f"Copy+remove successful: {src_path} -> {dst_path}")
            return True

        except OSError as e:
            # Clean up temp file on failure
            if temp_dst.exists():
                temp_dst.unlink(missing_ok=True)
            # Re-raise with better context
            if e.errno == 28:  # No space left on device
                raise InsufficientStorageError(
                    f"No space left on device during copy: {e}", required_space=src_path.stat().st_size, available_space=shutil.disk_usage(dst_path.parent).free
                )
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
    processor_name: Optional[str] = None,
    video_dir: Path = VIDEO_DIR,
    save: bool = True,
    delete_source: bool = False,
    **kwargs,
) -> "VideoFile":
    """
    Creates a VideoFile instance from a given video file path with improved error handling.

    Raises:
        InsufficientStorageError: When not enough disk space
        TranscodingError: When video transcoding fails
        ValueError: When required objects (Center, Processor) not found
        RuntimeError: For other processing errors
    """
    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.medical.hardware import EndoscopyProcessor

    original_file_name = file_path.name
    original_suffix = file_path.suffix
    final_storage_path = None
    transcoded_file_path = None

    try:
        # Ensure we operate under the canonical video path root
        data_paths = _get_data_paths()
        resolved_video_dir = _get_path(data_paths, "video", video_dir)
        video_dir = Path(resolved_video_dir)
        storage_root_default = Path(video_dir).parent
        resolved_storage_root = _get_path(data_paths, "storage", storage_root_default)
        storage_root = Path(resolved_storage_root)
        storage_root.mkdir(parents=True, exist_ok=True)

        # Check storage capacity before starting any work
        check_storage_capacity(file_path, storage_root)

        # 1. Transcode if necessary
        logger.debug("Checking transcoding requirement for %s", file_path)
        temp_transcode_dir = TMP_VIDEO_DIR / "transcoding"
        temp_transcode_dir.mkdir(parents=True, exist_ok=True)

        # Use a unique name for the potential transcoded file
        temp_transcoded_output_path = temp_transcode_dir / f"{uuid.uuid4()}{original_suffix}"

        try:
            transcoded_file_path = transcode_videofile_if_required(input_path=file_path, output_path=temp_transcoded_output_path)
            if transcoded_file_path is None:
                raise TranscodingError(f"Transcoding check/process failed for {file_path}")
        except Exception as e:
            raise TranscodingError(f"Video transcoding failed: {e}") from e

        logger.debug("Using file for hashing: %s", transcoded_file_path)

        # 2. Calculate hash (this will be the raw_video_hash)
        video_hash = get_video_hash(transcoded_file_path)
        if not video_hash:
            raise ValueError(f"Could not calculate video hash for {transcoded_file_path}")
        logger.info("Calculated raw video hash: %s for %s", video_hash, original_file_name)

        # 3. Check if hash already exists
        if cls_model.check_hash_exists(video_hash=video_hash):
            existing_video = cls_model.objects.get(video_hash=video_hash)
            logger.warning("Video with hash %s already exists (UUID: %s)", video_hash, existing_video.uuid)

            # Check if the existing video has a valid file
            existing_raw_path = existing_video.get_raw_file_path()
            if existing_video.has_raw and existing_raw_path and existing_raw_path.exists():
                logger.warning("Video with hash %s already exists and file is present. Returning existing instance.", video_hash)
                # Clean up transcoded file if it was created temporarily
                if transcoded_file_path != file_path and transcoded_file_path.exists():
                    transcoded_file_path.unlink(missing_ok=True)
                return existing_video

            logger.warning("Video with hash %s exists but file is missing. Deleting orphaned record.", video_hash)
            existing_video.delete()

        # 4. Generate UUID and final storage path
        new_file_name, uuid_val = get_uuid_filename(transcoded_file_path)
        final_storage_path = video_dir / new_file_name
        final_storage_path.parent.mkdir(parents=True, exist_ok=True)

        # 5. Move or Copy the file to final storage using improved method
        try:
            if delete_source and transcoded_file_path == file_path:
                logger.debug("Moving original file %s to %s", file_path, final_storage_path)
                atomic_move_with_fallback(file_path, final_storage_path)
            elif delete_source and transcoded_file_path != file_path:
                logger.debug("Moving transcoded file %s to %s", transcoded_file_path, final_storage_path)
                atomic_move_with_fallback(transcoded_file_path, final_storage_path)
            else:
                logger.debug("Copying file %s to %s", transcoded_file_path, final_storage_path)
                atomic_copy_with_fallback(transcoded_file_path, final_storage_path)
                if transcoded_file_path != file_path and transcoded_file_path.exists():
                    logger.debug("Cleaning up temporary transcoded file %s", transcoded_file_path)
                    transcoded_file_path.unlink(missing_ok=True)
        except InsufficientStorageError:
            # Re-raise storage errors as-is
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to move file to final storage: {e}") from e

        # 6. Verify hash after move/copy
        final_hash = get_video_hash(final_storage_path)
        if final_hash != video_hash:
            logger.error("Hash mismatch after file operation! Expected %s, got %s", video_hash, final_hash)
            final_storage_path.unlink(missing_ok=True)
            raise RuntimeError(f"Hash mismatch after file operation for {final_storage_path}")

        # 7. Get related objects
        try:
            center = Center.objects.get(name=center_name)
            processor = EndoscopyProcessor.objects.get(name=processor_name) if processor_name else None
            logger.debug("Found Center: %s, Processor: %s", center.name, processor.name if processor else "None")
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
        logger.info("Creating new VideoFile instance with UUID: %s", uuid_val)
        # Store FileField path relative to storage root including the videos prefix
        storage_base = Path(_get_path(data_paths, "storage", final_storage_path.parent))
        relative_name = (final_storage_path.relative_to(storage_base)).as_posix()
        video = cls_model(
            uuid=uuid_val,
            raw_file=relative_name,
            processed_file=None,
            center=center,
            processor=processor,
            original_file_name=original_file_name,
            video_hash=video_hash,
            processed_video_hash=None,
            suffix=original_suffix,
        )

        # 9. Save the instance if requested
        if save:
            logger.info("Saving new VideoFile instance (UUID: %s)", uuid_val)
            video.save()
            logger.info("Successfully created VideoFile PK %s (UUID: %s)", video.pk, video.uuid)

        return video

    except (InsufficientStorageError, TranscodingError, ValueError):
        # Re-raise these specific errors as-is
        raise
    except Exception as e:
        logger.error("Failed to create VideoFile from %s: (%s) %s", file_path, type(e).__name__, e, exc_info=True)
        # Clean up any created files
        if final_storage_path and final_storage_path.exists():
            logger.warning("Cleaning up orphaned file: %s", final_storage_path)
            final_storage_path.unlink(missing_ok=True)
        if transcoded_file_path and transcoded_file_path != file_path and transcoded_file_path.exists():
            logger.warning("Cleaning up orphaned transcoded file: %s", transcoded_file_path)
            transcoded_file_path.unlink(missing_ok=True)
        raise RuntimeError(f"Video processing failed: {e}") from e
