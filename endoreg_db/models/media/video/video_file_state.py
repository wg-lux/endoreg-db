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

    state, created = VideoState.objects.get_or_create(video_file=video)
    if created:
        logger.info("Created new VideoState for VideoFile %s", video.uuid)
    video.state = state
    # Avoid saving if called from within the save method itself
    if not getattr(video, '_saving', False):
        video.save(update_fields=['state'])
    return state

def _set_frames_extracted(video: "VideoFile", value: bool = True):
    """Sets the frames_extracted flag in the VideoState."""
    state = _get_or_create_state(video)
    if state.frames_extracted != value:
        state.frames_extracted = value
        state.save(update_fields=['frames_extracted'])
        logger.info("Set state.frames_extracted to %s for video %s", value, video.uuid)

