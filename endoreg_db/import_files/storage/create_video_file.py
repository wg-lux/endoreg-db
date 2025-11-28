# endoreg_db/import_files/storage/create_video_file.py
import logging
from typing import Tuple

from django.db import IntegrityError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import VideoFile
from endoreg_db.models.media.storage.processing_history import ProcessingHistory
from endoreg_db.utils.hashs import get_video_hash

logger = logging.getLogger(__name__)


def create_or_retrieve_video_file(
    ctx: ImportContext,
) -> Tuple[VideoFile, str, bool]:
    """
    Create a new or retrieve an existing VideoFile for the given context.

    Returns:
        video     : VideoFile instance
        file_hash : hash used for deduplication (video_hash)
        retry     : whether we are re-processing an existing file
    """
    file_path = ctx.file_path
    center_name = ctx.center_name
    processor_name = ctx.processor_name
    delete_source = ctx.delete_source
    retry = ctx.retry
    video_hash = ctx.file_hash

    # Ensure we have a hash for deduplication
    if not video_hash:
        video_hash = get_video_hash(file_path)
        ctx.file_hash = video_hash

    existing: VideoFile | None = None

    try:
        existing = VideoFile.objects.get(video_hash=video_hash)
    except VideoFile.DoesNotExist:
        existing = None

    # === NON-RETRY PATH WITH EXISTING VIDEO ===
    if existing and not retry:
        logger.info("Found existing VideoFile %s", existing.video_hash)

        # If your VideoFile has a "processed/anonymized" indicator, check it here
        if getattr(existing, "anonymized", False):
            logger.info(
                "Existing VideoFile %s already processed - short-circuiting",
                existing.video_hash,
            )
            ProcessingHistory.get_or_create_history(
                object_id=existing.pk,
                file_hash=video_hash,
                success=True,
            )
            return existing, video_hash, False

        logger.info(
            "Reprocessing existing VideoFile %s (not fully anonymized yet)",
            existing.video_hash,
        )
        ProcessingHistory.get_or_create_history(
            object_id=existing.pk,
            file_hash=video_hash,
            success=False,
        )
        return existing, video_hash, True

    # === CREATE OR RETRY PATH ===
    logger.info("Creating or retrieving VideoFile instance...")

    try:
        if not retry:
            # Adapt kwargs to your actual create helper
            video = VideoFile.create_from_file_initialized(
                file_path=file_path,
                center_name=center_name,
                processor_name=processor_name,
                delete_source=delete_source,
            )
        else:
            video = VideoFile.objects.get(video_hash=video_hash)
            logger.info(
                "Retrying import for existing VideoFile %s",
                video.video_hash,
            )

            if getattr(video, "anonymized", False):
                logger.info(
                    "Existing VideoFile %s already processed during retry - short-circuiting",
                    video.video_hash,
                )
                ProcessingHistory.get_or_create_history(
                    object_id=video.pk,
                    file_hash=video_hash,
                    success=True,
                )
                return video, video_hash, False

        if not video:
            raise RuntimeError("Failed to create VideoFile instance")

        logger.info("Video instance ready: %s", video.video_hash)

        ProcessingHistory.get_or_create_history(
            object_id=video.pk,
            file_hash=video_hash,
            success=bool(getattr(video, "anonymized", False)),
        )

        return video, video_hash, retry

    except IntegrityError:
        # Race condition - another worker created it first
        video = VideoFile.objects.get(video_hash=video_hash)
        logger.info(
            "Race condition detected, using existing VideoFile %s instead",
            video.video_hash,
        )

        ProcessingHistory.get_or_create_history(
            object_id=video.pk,
            file_hash=video_hash,
            success=bool(getattr(video, "anonymized", False)),
        )

        return video, video_hash, True
