from pathlib import Path
from typing import TYPE_CHECKING, Optional, Type
import shutil
import logging
import uuid
# Use the consolidated VIDEO_DIR from utils
from ...utils import VIDEO_DIR, TMP_VIDEO_DIR, data_paths  # Assuming VIDEO_DIR is here

if TYPE_CHECKING:
    from endoreg_db.models import Center, EndoscopyProcessor, VideoFile

# Import necessary utils
from ....utils.video.ffmpeg_wrapper import transcode_videofile_if_required
from ....utils.hashs import get_video_hash
from ....utils.file_operations import get_uuid_filename

logger = logging.getLogger(__name__)

def _create_from_file(
    cls_model: Type["VideoFile"],  # Pass the VideoFile class
    file_path: Path,
    center_name: str,  # Changed from center: Optional["Center"]
    processor_name: Optional[str] = None,
    video_dir: Path = VIDEO_DIR,  # This is the directory for the RAW file
    save: bool = True,
    delete_source: bool = False,
    **kwargs  # Added to catch potential extra arguments like 'center' object if passed
) -> "VideoFile":
    """
    Creates a VideoFile instance from a given video file path.

    Handles transcoding (if necessary), hashing, file storage, and database record creation.
    Raises exceptions on failure.
    """
    # Ensure related models are imported for runtime use if needed within the function

    from endoreg_db.models.administration.center.center import Center
    from endoreg_db.models.medical.hardware import EndoscopyProcessor
    original_file_name = file_path.name
    original_suffix = file_path.suffix
    final_storage_path = None # Initialize
    transcoded_file_path = None # Initialize

    try:
        # 1. Transcode if necessary
        logger.debug("Checking transcoding requirement for %s", file_path)
        temp_transcode_dir = TMP_VIDEO_DIR / 'transcoding'
        temp_transcode_dir.mkdir(parents=True, exist_ok=True)
        # Use a unique name for the potential transcoded file
        temp_transcoded_output_path = temp_transcode_dir / f"{uuid.uuid4()}{original_suffix}"

        transcoded_file_path = transcode_videofile_if_required(
            input_path=file_path,
            output_path=temp_transcoded_output_path # Provide a target path
        )
        if transcoded_file_path is None:
             raise RuntimeError(f"Transcoding check/process failed for {file_path}")

        logger.debug("Using file for hashing: %s", transcoded_file_path)

        # 2. Calculate hash (this will be the raw_video_hash)
        video_hash = get_video_hash(transcoded_file_path)
        if not video_hash:
             raise ValueError(f"Could not calculate video hash for {transcoded_file_path}")
        logger.info("Calculated raw video hash: %s for %s", video_hash, original_file_name)

        # 3. Check if hash already exists (checks raw hash)
        if cls_model.check_hash_exists(video_hash=video_hash):
            existing_video = cls_model.objects.get(video_hash=video_hash)
            logger.warning("Video with hash %s already exists (UUID: %s). Returning existing instance.", video_hash, existing_video.uuid)
            if existing_video.has_raw and existing_video.get_raw_file_path().exists():
                logger.warning("Video with hash %s already exists and file is present. Returning existing instance.", video_hash)
                return existing_video
            else:
                logger.warning("Video with hash %s exists but file is missing. Creating new instance.", video_hash)
            # Clean up transcoded file if it was created temporarily and is different from source
            if transcoded_file_path != file_path and transcoded_file_path.exists():
                transcoded_file_path.unlink(missing_ok=True)
            # Clean up the potentially empty output path if transcoding wasn't needed but path was provided
            if transcoded_file_path == file_path and temp_transcoded_output_path.exists():
                 temp_transcoded_output_path.unlink(missing_ok=True)
            return existing_video

        # 4. Prepare final storage path (for the raw file)
        new_file_name, uuid_val = get_uuid_filename(transcoded_file_path) # Use UUID filename based on the file we'll store
        final_storage_path = video_dir / new_file_name  # Path in VIDEO_DIR
        final_storage_path.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists

        # 5. Move or Copy the (potentially transcoded) file to final storage
        # Use the 'transcoded_file_path' which points to the correct source file now
        if delete_source and transcoded_file_path == file_path:
            # Move the original source file if delete_source is True AND no transcoding happened
            logger.debug("Moving original file %s to %s", file_path, final_storage_path)
            shutil.move(str(file_path), str(final_storage_path))
        elif delete_source and transcoded_file_path != file_path:
            # Move the temporary transcoded file if delete_source is True AND transcoding happened
            logger.debug("Moving transcoded file %s to %s", transcoded_file_path, final_storage_path)
            shutil.move(str(transcoded_file_path), str(final_storage_path))
            # Original source file remains untouched unless delete_source implies deleting the *original* input
            # If delete_source means delete the *original* input file path:
            # logger.debug("Deleting original source file %s after transcoding and moving.", file_path)
            # file_path.unlink(missing_ok=True) # Be careful with this interpretation
        else: # Copy scenario (delete_source is False)
            logger.debug("Copying file %s to %s", transcoded_file_path, final_storage_path)
            shutil.copy2(str(transcoded_file_path), str(final_storage_path))
            # Clean up temporary transcoded file if it was created and is different from source
            if transcoded_file_path != file_path and transcoded_file_path.exists():
                logger.debug("Deleting temporary transcoded file %s after copying.", transcoded_file_path)
                transcoded_file_path.unlink(missing_ok=True)

        # 6. Verify hash after move/copy (optional but recommended)
        final_hash = get_video_hash(final_storage_path)
        if final_hash != video_hash:
            logger.error("Hash mismatch after file operation for %s! Expected %s, got %s", final_storage_path, video_hash, final_hash)
            final_storage_path.unlink(missing_ok=True)  # Clean up corrupted file
            raise RuntimeError(f"Hash mismatch after file operation for {final_storage_path}")
        logger.debug("Hash verified for %s after file operation.", final_storage_path)

        # 7. Get related Center and Processor objects
        try:
            center = Center.objects.get(name=center_name)
            processor = EndoscopyProcessor.objects.get(name=processor_name) if processor_name else None
            logger.debug("Found Center: %s, Processor: %s for new video %s", center.name, processor.name if processor else "None", uuid_val)
        except Center.DoesNotExist as e:
            logger.error("Center '%s' not found for video %s.", center_name, uuid_val)
            # Clean up the stored file before raising
            if final_storage_path and final_storage_path.exists():
                 final_storage_path.unlink(missing_ok=True)
            raise ValueError(f"Center '{center_name}' not found.") from e
        except EndoscopyProcessor.DoesNotExist as e:
            logger.error("Processor '%s' not found for video %s.", processor_name, uuid_val)
             # Clean up the stored file before raising
            if final_storage_path and final_storage_path.exists():
                 final_storage_path.unlink(missing_ok=True)
            raise ValueError(f"Processor '{processor_name}' not found.") from e

        # 8. Create the VideoFile instance
        logger.info("Creating new VideoFile instance with UUID: %s", uuid_val)
        video = cls_model(
            uuid=uuid_val,
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
            logger.info("Saving new VideoFile instance (UUID: %s)", uuid_val)
            video.save()
            logger.info("Successfully created and saved VideoFile PK %s (UUID: %s)", video.pk, video.uuid)

        return video

    except Exception as e:
        logger.error("Failed to create VideoFile from %s: (%s) %s", file_path, type(e).__name__, e, exc_info=True)
        # Clean up potentially created file in final storage
        if final_storage_path and final_storage_path.exists():
            logger.warning("Cleaning up potentially orphaned file: %s", final_storage_path)
            final_storage_path.unlink(missing_ok=True)
        # Clean up temporary transcoded file if it still exists and is different from source
        if transcoded_file_path and transcoded_file_path != file_path and transcoded_file_path.exists():
             logger.warning("Cleaning up potentially orphaned transcoded file: %s", transcoded_file_path)
             transcoded_file_path.unlink(missing_ok=True)
        raise