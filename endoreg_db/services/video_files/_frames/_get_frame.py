# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def _get_frame(video: "VideoFile", frame_number: int) -> "Frame":
    """Gets a specific Frame object by its frame number."""
    from endoreg_db.models.media.frame.frame import Frame

    try:
        # Access related manager directly
        return video.frames.get(frame_number=frame_number)
    except AttributeError:
        logger.error(
            "Could not access frame %d for video %s via related manager.",
            frame_number,
            video.video_hash,
        )
        # Fallback query
        return Frame.objects.get(video=video, frame_number=frame_number)
    except Frame.DoesNotExist:
        logger.error("Frame %d not found for video %s.", frame_number, video.video_hash)
        raise  # Re-raise DoesNotExist
    except Exception as e:
        logger.error(
            "Error getting frame %d for video %s: %s",
            frame_number,
            video.video_hash,
            e,
            exc_info=True,
        )
        raise  # Re-raise other exceptions
