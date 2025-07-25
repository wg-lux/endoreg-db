import logging
from rest_framework import serializers
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer

logger = logging.getLogger(__name__)

def _validate(self:"LabelVideoSegmentSerializer", attrs):
    """
    Validates input data for a video segment, ensuring either time or frame information is provided and logically consistent.
    """
    logger.debug("Validation started")
    video_id = attrs.get('video_id') or self.initial_data.get('video_id') or self.initial_data.get('video_file')
    label_id = attrs.get('label_id') or self.initial_data.get('label_id') or self.initial_data.get('label')
    logger.debug(f"Validating video segment: video_id={video_id}, label_id={label_id}, attrs={ {k: v for k, v in attrs.items() if k not in ['video_file', 'label']} }")

    instance = getattr(self, 'instance', None)
    # Frame numbers
    start_frame_number = attrs.get('start_frame_number')
    end_frame_number = attrs.get('end_frame_number')
    if instance is not None:
        if start_frame_number is None:
            start_frame_number = getattr(instance, 'start_frame_number', None)
        if end_frame_number is None:
            end_frame_number = getattr(instance, 'end_frame_number', None)
    # Times
    start_time = attrs.get('start_time', None)
    end_time = attrs.get('end_time', None)
    if instance is not None:
        if start_time is None:
            start_time = getattr(instance, 'start_time', None)
        if end_time is None:
            end_time = getattr(instance, 'end_time', None)

    # Always convert start_time/end_time to float if present
    if 'start_time' in self.initial_data:
        try:
            attrs['start_time'] = float(self.initial_data['start_time'])
        except (TypeError, ValueError):
            attrs['start_time'] = None
        start_time = attrs['start_time']
    elif start_time is not None:
        try:
            start_time = float(start_time)
            attrs['start_time'] = start_time
        except (TypeError, ValueError):
            start_time = None
            attrs['start_time'] = None
    if 'end_time' in self.initial_data:
        try:
            attrs['end_time'] = float(self.initial_data['end_time'])
        except (TypeError, ValueError):
            attrs['end_time'] = None
        end_time = attrs['end_time']
    elif end_time is not None:
        try:
            end_time = float(end_time)
            attrs['end_time'] = end_time
        except (TypeError, ValueError):
            end_time = None
            attrs['end_time'] = None

    has_time_data = start_time is not None and end_time is not None
    has_frame_data = start_frame_number is not None and end_frame_number is not None

    if not has_time_data and not has_frame_data:
        raise serializers.ValidationError(
            "Either start_time/end_time or start_frame_number/end_frame_number must be provided"
        )

    label_id = attrs.get('label_id')
    label_name = attrs.get('label_name')
    if not label_id and not label_name:
        logger.info("Creating segment without label")

    if has_time_data:
        if start_time < 0:
            raise serializers.ValidationError("start_time must be non-negative")
        if end_time <= start_time:
            raise serializers.ValidationError("end_time must be greater than start_time")

    if has_frame_data:
        if start_frame_number < 0:
            raise serializers.ValidationError("start_frame_number must be non-negative")
        if end_frame_number <= start_frame_number:
            raise serializers.ValidationError("end_frame_number must be greater than start_frame_number")

    logger.debug(f"Attributes after validation: {attrs}")
    return attrs
