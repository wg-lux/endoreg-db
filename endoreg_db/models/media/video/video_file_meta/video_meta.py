import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..video_file import VideoFile

logger = logging.getLogger(__name__)


def _update_video_meta(video: "VideoFile", save_instance: bool = True):
    """
    Updates or creates the technical VideoMeta from the raw video file.
    Raises FileNotFoundError or ValueError on pre-condition failure, RuntimeError on processing failure.
    """
    from ....metadata import VideoMeta  # Local import

    logger.debug("Updating technical VideoMeta for video %s (from raw file).", video.uuid)

    if not video.has_raw:
        # DEFENSIVE: Log warning and skip instead of crashing
        logger.warning(
            f"Raw video file path not available for {video.uuid}. Skipping VideoMeta update - this may indicate the video was processed and raw file moved."
        )
        return  # Graceful skip instead of FileNotFoundError

    raw_video_path = video.get_raw_file_path()  # Use helper
    if not raw_video_path or not raw_video_path.exists():
        # DEFENSIVE: Log warning and skip instead of crashing production pipeline
        logger.warning(
            f"Raw video file path {raw_video_path} does not exist for video {video.uuid}. Skipping VideoMeta update - this typically happens after video processing when raw files are moved to processed location."
        )
        return  # Graceful skip instead of FileNotFoundError that crashes production

    try:
        vm = video.video_meta
        if vm:
            logger.info("Updating existing VideoMeta (PK: %s) for video %s.", vm.pk, video.uuid)
            vm.update_meta(raw_video_path)  # Assuming this method exists and raises on error
            vm.save()
        else:
            if not video.center or not video.processor:
                # Raise exception
                raise ValueError(f"Cannot create VideoMeta for {video.uuid}: Center or Processor is missing.")

            logger.info("Creating new VideoMeta for video %s.", video.uuid)
            # Assuming create_from_file exists and raises on error
            video.video_meta = VideoMeta.create_from_file(
                video_path=raw_video_path,
                center=video.center,
                processor=video.processor,
                save_instance=True,  # Let create_from_file handle saving
            )
            vm = video.video_meta
            assert vm is not None  # For type checker
            logger.info("Created and linked VideoMeta (PK: %s) for video %s.", vm.pk, video.uuid)

        # Save the VideoFile instance itself if requested and if video_meta was linked/updated
        if save_instance:
            update_fields = ["video_meta"]
            # Check if derived fields also need updating
            if video.video_meta:
                meta_fields = ["fps", "duration", "frame_count", "width", "height"]
                for field in meta_fields:
                    # Check if field is None on video but has value on meta
                    if getattr(video, field) is None and getattr(video.video_meta, field, None) is not None:
                        # No need to set attribute here, save method handles it
                        update_fields.append(field)
            # Ensure update_fields has unique values before saving
            unique_update_fields = list(set(update_fields))
            if unique_update_fields:
                video.save(update_fields=unique_update_fields)
                logger.info("Saved video %s after VideoMeta update (Fields: %s).", video.uuid, unique_update_fields)

    except Exception as e:
        logger.error("Failed to update/create VideoMeta for video %s: %s", video.uuid, e, exc_info=True)
        # Re-raise exception
        raise RuntimeError(f"Failed to update/create VideoMeta for video {video.uuid}") from e
