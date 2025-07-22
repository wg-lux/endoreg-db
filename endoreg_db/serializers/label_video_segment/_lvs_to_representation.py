from typing import TYPE_CHECKING
from endoreg_db.models import (
    VideoFile,
)

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer


def _to_representation(self:"LabelVideoSegmentSerializer", instance):
    """
    Returns a JSON-compatible representation of the label video segment, including calculated start and end times in seconds and the label name.
    
    Adds `start_time` and `end_time` fields by converting frame numbers to seconds using the video's FPS, defaulting to zero if unavailable. Ensures `label_name` is always included in the output.
    """
    data = super().to_representation(instance)
    
    video_file = instance.video_file
    assert video_file is not None, "Video file must be associated with the segment"
    assert isinstance(video_file, VideoFile), "Expected video_file to be an instance of VideoFile"
    # Add calculated time fields for frontend compatibility
    
    data['start_time'] = video_file.frame_number_to_s(instance.start_frame_number)
    data['end_time'] = video_file.frame_number_to_s(instance.end_frame_number)
    
    # Ensure label_name is always present in response
    if instance.label:
        data['label_name'] = instance.label.name
    else:
        data['label_name'] = None

    # Explicitly add video_id and label_id to the output
    data['video_id'] = instance.video_file.id if instance.video_file else None
    data['label_id'] = instance.label.id if instance.label else None

    return data