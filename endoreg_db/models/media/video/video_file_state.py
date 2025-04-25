import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .video_file import VideoFile
    from ...state import VideoState

logger = logging.getLogger(__name__)

def _get_or_create_state(video: "VideoFile") -> "VideoState":
    """Gets or creates the VideoState associated with the VideoFile."""
    from ...state import VideoState # Local import

    if video.state:
        return video.state

    # Use get_or_create which handles the check and creation atomically
    state, created = VideoState.objects.get_or_create(video_file=video)
    if created:
        logger.info("Created new VideoState for VideoFile %s", video.uuid)
        # Link the state back to the video instance in memory
        video.state = state
        # No need to save video here, the caller (usually video.save()) handles it
    elif not video.state:
        # If state existed but wasn't linked on the instance, link it
        video.state = state

    return state

