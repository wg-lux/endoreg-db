
from typing import TYPE_CHECKING

import logging

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile
    
logger = logging.getLogger(__name__)




def _get_frame_number(video: "VideoFile") -> int:
    """Counts the number of associated Frame objects in the database."""
    try:
        # Access related manager directly
        return video.frames.count()
    except AttributeError:
        logger.error("Could not access frame count for video %s. 'frames' related manager not found.", str(video))
        # Fallback query (less efficient)
        frame_model = video.get_frame_model()
        return frame_model.objects.filter(video_file=video).count()
    except Exception as e:
        logger.error("Error counting frames for video %s: %s", video.uuid, e, exc_info=True)
        return 0
    
