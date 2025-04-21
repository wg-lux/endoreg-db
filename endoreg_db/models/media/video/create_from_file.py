from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type
import shutil
import logging

# Use the consolidated VIDEO_DIR from utils
from ...utils import VIDEO_DIR, data_paths  # Assuming VIDEO_DIR is here

if TYPE_CHECKING:
    # Import the new consolidated model
    from .video_file import VideoFile

# Import necessary utils
from ....utils.video import transcode_videofile_if_required
from ....utils.hashs import get_video_hash
from ....utils.file_operations import get_uuid_filename

logger = logging.getLogger(__name__)

def _create_from_file(
    cls_model: Type["VideoFile"],  # Pass the VideoFile class
    file_path: Path,
    center_name: str,
    processor_name: Optional[str] = None,
    video_dir: Path = VIDEO_DIR,  # This is the directory for the RAW file
    save: bool = True,
    delete_source: bool = False,
) -> "VideoFile":
    """
    Helper function to create a VideoFile instance from a source file.

    Handles transcoding, hashing, file moving/copying, and instance creation.
    The created instance will initially only have the `raw_file` populated.
    """
    # Import models inside function to avoid potential circular imports at module level
    from ...administration.center import Center
    from ...medical.hardware import EndoscopyProcessor

    if not file_path.exists():
        logger.error("Source video file not found: %s", file_path)
        raise FileNotFoundError(f"Source video file not found: {file_path}")

    video_dir.mkdir(parents=True, exist_ok=True)  # Ensure raw video dir exists
    original_file_name = file_path.name
    original_suffix = file_path.suffix
    logger.info("Processing video file: %s for center: %s (as raw)", original_file_name, center_name)

    transcoded_file_path = None  # Initialize
    final_storage_path = None  # Initialize
    try:
        # 1. Transcode if necessary
        logger.debug("Checking transcoding requirement for %s", file_path)
        transcoded_file_path = transcode_videofile_if_required(file_path)
        logger.debug("Using file for hashing: %s", transcoded_file_path)

        # 2. Calculate hash (this will be the raw_video_hash)
        video_hash = get_video_hash(transcoded_file_path)
        logger.info("Calculated raw video hash: %s for %s", video_hash, original_file_name)

        # 3. Check if hash already exists (checks raw hash)
        if cls_model.check_hash_exists(video_hash=video_hash):
            existing_video = cls_model.objects.get(video_hash=video_hash)
            logger.warning("Video with hash %s already exists (UUID: %s). Returning existing instance.", video_hash, existing_video.uuid)
            # Clean up transcoded file if it was created temporarily
            if transcoded_file_path != file_path:
                transcoded_file_path.unlink(missing_ok=True)
            return existing_video

        # 4. Prepare final storage path (for the raw file)
        new_file_name, uuid = get_uuid_filename(transcoded_file_path)  # Use UUID filename
        final_storage_path = video_dir / new_file_name  # Path in VIDEO_DIR
        logger.debug("Target raw storage path: %s", final_storage_path)

        # 5. Move or copy the file to final storage (raw file location)
        if transcoded_file_path != file_path and delete_source:
            logger.debug("Moving transcoded file %s to %s and deleting original %s", transcoded_file_path, final_storage_path, file_path)
            shutil.move(transcoded_file_path, final_storage_path)
            file_path.unlink(missing_ok=True)  # Delete original source
        elif transcoded_file_path == file_path and delete_source:
            logger.debug("Moving original file %s to %s", file_path, final_storage_path)
            shutil.move(file_path, final_storage_path)
        else:  # Copy scenario (delete_source is False)
            logger.debug("Copying file %s to %s", transcoded_file_path, final_storage_path)
            shutil.copy2(transcoded_file_path, final_storage_path)
            # Clean up temporary transcoded file if it was created
            if transcoded_file_path != file_path:
                logger.debug("Deleting temporary transcoded file %s", transcoded_file_path)
                transcoded_file_path.unlink(missing_ok=True)

        # 6. Verify hash after move/copy (optional but recommended)
        final_hash = get_video_hash(final_storage_path)
        if final_hash != video_hash:
            logger.error("Hash mismatch after file operation! Expected %s, got %s for %s", video_hash, final_hash, final_storage_path)
            final_storage_path.unlink(missing_ok=True)  # Clean up corrupted file
            raise ValueError("Hash mismatch after file operation.")
        logger.debug("Hash verified after file operation.")

        # 7. Get related Center and Processor objects
        try:
            center = Center.objects.get(name=center_name)
            processor = EndoscopyProcessor.objects.get(name=processor_name) if processor_name else None
            logger.debug("Found Center: %s, Processor: %s", center.name, processor.name if processor else "None")
        except Center.DoesNotExist:
            logger.error("Center '%s' not found.", center_name)
            raise
        except EndoscopyProcessor.DoesNotExist:
            logger.error("Processor '%s' not found.", processor_name)
            raise

        # 8. Create the VideoFile instance
        logger.info("Creating new VideoFile instance with UUID: %s", uuid)
        video = cls_model(
            uuid=uuid,
            # Store relative path from STORAGE_DIR for raw_file field
            raw_file=final_storage_path.relative_to(data_paths['storage']).as_posix(),
            processed_file=None,  # Initially no processed file
            center=center,
            processor=processor,
            original_file_name=original_file_name,
            video_hash=video_hash,  # This is the hash of the raw file
            processed_video_hash=None,  # Initially no processed hash
            suffix=original_suffix,
            # Other fields will be populated by save() method or later updates
        )

        # 9. Save the instance if requested
        if save:
            logger.info("Saving new VideoFile instance (UUID: %s)", uuid)
            video.save()
            logger.info("Successfully created and saved VideoFile PK %s", video.pk)

        return video

    except Exception as e:
        logger.error("Failed to create VideoFile from %s: %s", file_path, e, exc_info=True)
        # Clean up potentially created file in final storage
        if final_storage_path and final_storage_path.exists():
            logger.warning("Cleaning up potentially orphaned file: %s", final_storage_path)
            final_storage_path.unlink(missing_ok=True)
        # Clean up temporary transcoded file if it still exists
        if transcoded_file_path and transcoded_file_path != file_path and transcoded_file_path.exists():
            transcoded_file_path.unlink(missing_ok=True)
        raise  # Re-raise the exception