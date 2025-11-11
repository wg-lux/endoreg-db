import logging
from typing import TYPE_CHECKING

from django.db.models import QuerySet

if TYPE_CHECKING:
    from endoreg_db.models import Frame, VideoFile

logger = logging.getLogger(__name__)


def _get_frames(video: "VideoFile") -> "QuerySet[Frame]":
    """Gets a QuerySet of all associated Frame objects, ordered by frame number."""
    from endoreg_db.models import Frame

    try:
        # Access related manager directly
        return video.frames.order_by("frame_number")
    except AttributeError:
        logger.error("Could not access frames for video %s. 'frames' related manager not found.", video.uuid)
        # Fallback query
        return Frame.objects.filter(video_file=video).order_by("frame_number")
    except Exception as e:
        logger.error("Error getting frames for video %s: %s", video.uuid, e, exc_info=True)
        return Frame.objects.none()  # Return empty queryset on error
