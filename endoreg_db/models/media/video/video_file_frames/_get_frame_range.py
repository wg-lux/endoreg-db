import logging
from typing import TYPE_CHECKING

from django.db.models import QuerySet

if TYPE_CHECKING:
    from endoreg_db.models import Frame, VideoFile

logger = logging.getLogger(__name__)


def _get_frame_range(video: "VideoFile", start_frame_number: int, end_frame_number: int) -> "QuerySet[Frame]":
    """Gets a QuerySet of Frame objects within a specific range, ordered by frame number."""
    from endoreg_db.models import Frame

    try:
        # Access related manager directly
        return video.frames.filter(
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")
    except AttributeError:
        logger.error("Could not access frame range for video %s via related manager.", video.uuid)
        # Fallback query
        return Frame.objects.filter(
            video_file=video,
            frame_number__gte=start_frame_number,
            frame_number__lte=end_frame_number,
        ).order_by("frame_number")
    except Exception as e:
        logger.error("Error getting frame range (%d-%d) for video %s: %s", start_frame_number, end_frame_number, video.uuid, e, exc_info=True)
        return Frame.objects.none()  # Return empty queryset on error
