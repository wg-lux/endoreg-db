import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..video_file import VideoFile

logger = logging.getLogger(__name__)


def _get_import_processor(video: "VideoFile"):
    """
    Return the processor that should be used for import/anonymization context.

    ``VideoFile.processor`` is the canonical model field. ``VideoMeta.processor``
    is kept as a compatibility fallback for older rows that predate the direct
    VideoFile relation.
    """
    processor = video.processor
    if processor is not None:
        return processor

    video_meta = video.video_meta
    if video_meta is None:
        return None
    return video_meta.processor


def _get_import_context_names(video: "VideoFile") -> tuple[str, str]:
    """
    Resolve center and processor names from the VideoFile model graph.

    Center is required for import bookkeeping. Processor can be absent on legacy
    rows, so keep the historical "Unknown" fallback used by the anonymization
    path.
    """
    center = video.center
    if center is None or not center.name:
        raise ValueError(f"Video {video.video_hash} has no associated center.")

    processor = video.get_import_processor()
    processor_name = processor.name if processor is not None else None
    return str(center.name), str(processor_name or "Unknown")


def _populate_video_fields_from_meta(video: "VideoFile") -> list[str]:
    """
    Copy derived technical fields from ``video.video_meta`` onto ``video`` in memory.

    Returns the list of fields populated on the ``VideoFile`` instance.
    """
    if not video.video_meta:
        return []

    update_fields: list[str] = []
    meta_fields = ["fps", "duration", "frame_count", "width", "height"]
    for field in meta_fields:
        current_value = getattr(video, field)
        meta_value = getattr(video.video_meta, field, None)
        if current_value is None and meta_value is not None:
            setattr(video, field, meta_value)
            update_fields.append(field)
    return update_fields


def _update_video_meta(video: "VideoFile", save_instance: bool = True):
    """
    Updates or creates the technical VideoMeta from the raw video file.
    Raises FileNotFoundError or ValueError on pre-condition failure, RuntimeError on processing failure.
    """
    from ....metadata import VideoMeta  # Local import

    logger.debug(
        "Updating technical VideoMeta for video %s (from raw file).", video.video_hash
    )

    if not video.has_raw:
        # DEFENSIVE: Log warning and skip instead of crashing
        logger.warning(
            f"Raw video file path not available for {video.video_hash}. Skipping VideoMeta update - this may indicate the video was processed and raw file moved."
        )
        return  # Graceful skip instead of FileNotFoundError

    try:
        raw_context = video.ensure_local_raw_file()
    except (AttributeError, ValueError, FileNotFoundError):
        # DEFENSIVE: Log warning and skip instead of crashing production pipeline
        logger.warning(
            "Raw video file is not locally available for video %s. Skipping VideoMeta update.",
            video.video_hash,
        )
        return

    try:
        with raw_context as raw_video_path:
            if not raw_video_path.exists():
                # DEFENSIVE: Log warning and skip instead of crashing production pipeline
                logger.warning(
                    f"Raw video file path {raw_video_path} does not exist for video {video.video_hash}. Skipping VideoMeta update - this typically happens after video processing when raw files are moved to processed location."
                )
                return  # Graceful skip instead of FileNotFoundError that crashes production

            vm = video.video_meta
            if vm:
                logger.info(
                    "Updating existing VideoMeta (PK: %s) for video %s.",
                    vm.pk,
                    video.video_hash,
                )
                vm.update_meta(
                    raw_video_path
                )  # Assuming this method exists and raises on error
                vm.save()
            else:
                if not video.center or not video.processor:
                    # Raise exception
                    raise ValueError(
                        f"Cannot create VideoMeta for {video.video_hash}: Center or Processor is missing."
                    )

                logger.info("Creating new VideoMeta for video %s.", video.video_hash)
                # Assuming create_from_file exists and raises on error
                video.video_meta = VideoMeta.create_from_file(
                    video_path=raw_video_path,
                    center=video.center,
                    processor=video.processor,
                    save_instance=True,  # Let create_from_file handle saving
                )
                vm = video.video_meta
                assert vm is not None  # For type checker
                logger.info(
                    "Created and linked VideoMeta (PK: %s) for video %s.",
                    vm.pk,
                    video.video_hash,
                )

            # Save the VideoFile instance itself if requested and if video_meta was linked/updated
            update_fields = ["video_meta"]
            update_fields.extend(_populate_video_fields_from_meta(video))

            if save_instance:
                # Ensure update_fields has unique values before saving
                unique_update_fields = list(dict.fromkeys(update_fields))
                if unique_update_fields:
                    video.save(update_fields=unique_update_fields)
                    logger.info(
                        "Saved video %s after VideoMeta update (Fields: %s).",
                        video.video_hash,
                        unique_update_fields,
                    )

    except Exception as e:
        logger.error(
            "Failed to update/create VideoMeta for video %s: %s",
            video.video_hash,
            e,
            exc_info=True,
        )
        # Re-raise exception
        raise RuntimeError(
            f"Failed to update/create VideoMeta for video {video.video_hash}"
        ) from e
