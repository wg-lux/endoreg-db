
from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path, _get_temp_anonymized_frame_dir

from typing import TYPE_CHECKING, List

import logging

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, Frame
    
logger = logging.getLogger(__name__)



def _bulk_create_frames(video: "VideoFile", frames_to_create: List["Frame"]):
    """Helper function to perform bulk_create with ignore_conflicts."""
    from endoreg_db.models import Frame
    try:
        Frame.objects.bulk_create(frames_to_create, ignore_conflicts=True)
    except Exception as e:
        logger.error("Error during bulk creation of frames for video %s: %s", video.uuid, e, exc_info=True)
        raise