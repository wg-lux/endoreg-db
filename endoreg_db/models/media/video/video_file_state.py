"""
Contains helper functions related to the state management of VideoFile objects.
The actual VideoState model is defined in endoreg_db.models.state.video.
"""
# This file can contain helper functions that operate on VideoFile and its state
# For example:
# def check_if_ready_for_anonymization(video_file: "VideoFile") -> bool:
#     state = video_file.get_or_create_state()
#     # ... logic checking state flags ...
#     return True/False

# Currently, state logic is primarily handled within the VideoFile model itself
# or the specific process modules (e.g., anonymization checks its pre-conditions).

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

