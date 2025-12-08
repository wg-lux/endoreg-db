# endoreg_db/import_files/storage/create_video_file.py
import logging
from typing import Tuple

from django.db import IntegrityError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.ensure_center import ensure_center
from endoreg_db.models.media import VideoFile
from endoreg_db.models.media.storage.processing_history import ProcessingHistory
from endoreg_db.utils.hashs import get_video_hash

logger = logging.getLogger(__name__)


def create_or_retrieve_video_file(
    ctx: ImportContext,
) -> Tuple[VideoFile, bool]:
    """
    Create a new or retrieve an existing VideoFile for the given context.

    Returns:
        video     : VideoFile instance
        retry     : whether we are re-processing an existing file
    """
    file_path = ctx.file_path
    center_name = ctx.center_name
    processor_name = ctx.processor_name
    delete_source = ctx.delete_source
    file_type = ctx.file_type

    # 1) Determine the VideoFile instance to work with
    if ctx.current_video is not None:
        video = ctx.current_video
        logger.info("Using existing VideoFIle from context: pk =%s", video.pk)
    else:
        logger.info("Creating new VideoFIle from %s for center %s", file_path, center_name)
        video = VideoFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
        )
        center = ensure_center(video, ctx.center_name)
        
        logger.info(f"Successfully set up report file from {center.name}")
    # 3) Check if we already have a successful history entry for this object+file_type        
    has_success_history = ProcessingHistory.has_history_for_object(
        obj=video,
        success=True,
    )

    if has_success_history:
        logger.info(
            "VideoFile %s already has successful processing history. (file_type:%s) - short-circuiting",
            getattr(video, str(video.active_file_path)),
            file_type,
    )
    
    # 4) No successful history yet → ensure there is a history entry marking it as "in progress"/failed
    ProcessingHistory.get_or_create_for_object(
        obj=video,
        success=False
    )
    
    
    logger.info(
        "Video instance ready for processing: pk=%s, file_type=%s (needs_processing=True)",
        video.pk,
        file_type,
    )
    
    # Signal to the caller that the anonymization pipeline should run
    return video, True

