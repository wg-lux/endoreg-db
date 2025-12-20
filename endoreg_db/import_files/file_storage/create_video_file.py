# endoreg_db/import_files/storage/create_video_file.py
import logging
from typing import Tuple
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.ensure_center import ensure_center
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.models.media import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.import_files.file_storage.state_management import finalize_failure

logger = logging.getLogger(__name__)


def create_or_retrieve_video_file(
    ctx: ImportContext,
) -> Tuple[VideoFile, bool, bool]:
    """
    Create a new or retrieve an existing VideoFile for the given context.

    Returns:
        video           : VideoFile instance
        processed       : True if there is already a successful ProcessingHistory for this file
        needs_processing: True if the pipeline should run for this file in this call
    """
    file_path = ctx.file_path

    center_name = ctx.center_name
    processor_name = ctx.processor_name
    delete_source = ctx.delete_source
    file_type = ctx.file_type  # logical key for history; can be None

    # default assumptions (same semantics as report)
    processed = False
    needs_processing = True

    if not isinstance(ctx.file_hash, str):
        ctx.file_hash = sha256_file(ctx.file_path)

        # 2) Check if we already have a successful history entry for this object
    has_success_history = ProcessingHistory.has_history_for_hash(
        file_hash=ctx.file_hash,
        success=True,
    )
    has_failure_history = ProcessingHistory.has_history_for_hash(
        file_hash=ctx.file_hash,
        success=False,
    )

    if has_success_history:
        logger.info(
            "VideoFile already has successful processing history (file_hash=%s) "
            "- short-circuiting",
            ctx.file_hash,
        )
        processed = True
        needs_processing = False
        if not isinstance(ctx.current_video, VideoFile):
            ctx.current_video = VideoFile.get_video_by_content_hash(ctx.file_hash)
        return ctx.current_video, processed, needs_processing
    elif has_failure_history:
        if not isinstance(ctx.current_video, VideoFile):
            ctx.current_video = VideoFile.get_video_by_content_hash(ctx.file_hash)
        finalize_failure(ctx)

        processed = True
        needs_processing = True

    # Determine the VideoFile instance to work with
    if ctx.current_video is not None:
        video = ctx.current_video
        logger.info("Using existing VideoFile from context: pk=%s", video.pk)
    else:
        logger.info(
            "Creating new VideoFile from %s for center %s",
            file_path,
            center_name,
        )
        video = VideoFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
            video_hash=ctx.file_hash,
        )
        needs_processing = True

        center = ensure_center(video, ctx.center_name)
        logger.info("Successfully set up video file from %s", center.name)

    # No successful history yet → ensure there is a history entry marking it as "in progress"/failed
    ProcessingHistory.get_or_create_for_hash(
        file_hash=ctx.file_hash,
        success=False,
    )

    logger.info(
        "Video instance ready for processing: pk=%s, file_type=%s (needs_processing=True)",
        video.pk,
        file_type,
    )

    return video, processed, needs_processing
