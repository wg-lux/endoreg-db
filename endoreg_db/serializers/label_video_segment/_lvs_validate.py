import logging
from rest_framework import serializers
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer

logger = logging.getLogger(__name__)

def _validate(self:"LabelVideoSegmentSerializer", attrs):
    """
    Validates input data for a video segment, ensuring either time or frame information is provided and logically consistent.
    
    Checks that either start/end times or start/end frame numbers are present, and that start values are non-negative and end values are greater than start values. Allows segments without labels.
    """
    logger.debug("Validation started")
    # Avoid logging full objects that may raise errors
    video_id = attrs.get('video_id') or self.initial_data.get('video_id') or self.initial_data.get('video_file')
    label_id = attrs.get('label_id') or self.initial_data.get('label_id') or self.initial_data.get('label')
    logger.debug(f"Validating video segment: video_id={video_id}, label_id={label_id}, attrs={ {k: v for k, v in attrs.items() if k not in ['video_file', 'label']} }")

    # Check if we have either time or frame data
    has_time_data = 'start_time' in self.initial_data and 'end_time' in self.initial_data
    has_frame_data = 'start_frame_number' in attrs and 'end_frame_number' in attrs

    # PATCH: Copy start_time/end_time from initial_data to attrs if present
    if has_time_data:
        attrs['start_time'] = float(self.initial_data['start_time'])
        attrs['end_time'] = float(self.initial_data['end_time'])

    if not has_time_data and not has_frame_data:
        raise serializers.ValidationError(
            "Either start_time/end_time or start_frame_number/end_frame_number must be provided"
        )

    # Validate that we have either label_id or label_name
    label_id = attrs.get('label_id')
    label_name = attrs.get('label_name')

    if not label_id and not label_name:
        logger.info("Creating segment without label")

    # Validate time data if provided
    start_time = attrs.get('start_time')
    end_time = attrs.get('end_time')

    if has_time_data:
        if start_time is not None and float(start_time) < 0:
            raise serializers.ValidationError("start_time must be non-negative")
        if end_time is not None and float(end_time) <= float(start_time):
            raise serializers.ValidationError("end_time must be greater than start_time")

    # Validate frame data if provided
    if has_frame_data:
        if attrs['start_frame_number'] < 0:
            raise serializers.ValidationError("start_frame_number must be non-negative")
        if attrs['end_frame_number'] <= attrs['start_frame_number']:
            raise serializers.ValidationError("end_frame_number must be greater than start_frame_number")

    logger.debug(f"Attributes after validation: {attrs}")
    return attrs
