import logging
from rest_framework import serializers
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer

logger = logging.getLogger(__name__)

def _validate(self:"LabelVideoSegmentSerializer", attrs):
    """
    Validate video segment data, ensuring the presence and logical consistency of either time-based or frame-based segment information.
    
    Raises:
        ValidationError: If neither valid time nor frame data is provided, or if time/frame constraints are violated.
    
    Returns:
        dict: The validated attributes for the video segment.
    """
    logger.debug("Validation started")
    video_id, label_id = self.extract_and_validate_basic_attrs(attrs)
    instance = getattr(self, 'instance', None)
    start_time, end_time = self.process_time_data(attrs, instance)
    start_frame_number, end_frame_number = self.process_frame_data(attrs, instance)
    has_time_data, has_frame_data = self.validate_time_and_frame_presence(start_time, end_time, start_frame_number, end_frame_number)
    label_id = attrs.get('label_id')
    label_name = attrs.get('label_name')
    if not label_id and not label_name:
        logger.info("Creating segment without label")
    if has_time_data:
        self.validate_time_constraints(start_time, end_time)
    if has_frame_data:
        self.validate_frame_constraints(start_frame_number, end_frame_number)
    logger.debug(f"Attributes after validation: {attrs}")
    return attrs

def _extract_and_validate_basic_attrs(self:"LabelVideoSegmentSerializer", attrs):
    """
    Extract and return the video and label identifiers from the provided attributes or initial data.
    
    Returns:
        Tuple containing the resolved video ID and label ID.
    """
    video_id = attrs.get('video_id') or self.initial_data.get('video_id') or self.initial_data.get('video_file')
    label_id = attrs.get('label_id') or self.initial_data.get('label_id') or self.initial_data.get('label')
    logger.debug(f"Validating video segment: video_id={video_id}, label_id={label_id}, attrs={{k: v for k, v in attrs.items() if k not in ['video_file', 'label']}}")
    return video_id, label_id

def _process_time_data(self:"LabelVideoSegmentSerializer", attrs, instance):
    """
    Extract and convert start and end time values from input data, ensuring they are floats or None.
    
    Parameters:
        attrs (dict): Input attributes containing potential time values.
        instance: Existing object instance to use as a fallback for time values.
    
    Returns:
        tuple: A pair (start_time, end_time), each as a float or None if missing or invalid.
    """
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
    return start_time, end_time

def _process_frame_data(self:"LabelVideoSegmentSerializer", attrs, instance):
    """
    Retrieve start and end frame numbers from input attributes or an existing instance.
    
    Returns:
        tuple: A pair containing the start and end frame numbers, or None if not available.
    """
    start_frame_number = attrs.get('start_frame_number')
    end_frame_number = attrs.get('end_frame_number')
    if instance is not None:
        if start_frame_number is None:
            start_frame_number = getattr(instance, 'start_frame_number', None)
        if end_frame_number is None:
            end_frame_number = getattr(instance, 'end_frame_number', None)
    return start_frame_number, end_frame_number

def _validate_time_and_frame_presence(self:"LabelVideoSegmentSerializer", start_time, end_time, start_frame_number, end_frame_number):
    """
    Check that either both time values or both frame number values are provided for a video segment.
    
    Raises a ValidationError if neither a complete time range nor a complete frame range is present.
    
    Returns:
        has_time_data (bool): True if both start_time and end_time are provided.
        has_frame_data (bool): True if both start_frame_number and end_frame_number are provided.
    """
    has_time_data = start_time is not None and end_time is not None
    has_frame_data = start_frame_number is not None and end_frame_number is not None
    if not has_time_data and not has_frame_data:
        raise serializers.ValidationError(
            "Either start_time/end_time or start_frame_number/end_frame_number must be provided"
        )
    return has_time_data, has_frame_data

def _validate_time_constraints(self:"LabelVideoSegmentSerializer", start_time, end_time):
    """
    Validate that the start and end times for a video segment are logically consistent.
    
    Raises a ValidationError if start_time is negative or if end_time is not strictly greater than start_time.
    """
    if start_time < 0:
        raise serializers.ValidationError("start_time must be non-negative")
    if end_time <= start_time:
        raise serializers.ValidationError("end_time must be greater than start_time")

def _validate_frame_constraints(self:"LabelVideoSegmentSerializer", start_frame_number, end_frame_number):
    """
    Validate that frame number constraints are satisfied for a video segment.
    
    Raises a ValidationError if `start_frame_number` is negative or if `end_frame_number` is not greater than `start_frame_number`.
    """
    if start_frame_number < 0:
        raise serializers.ValidationError("start_frame_number must be non-negative")
    if end_frame_number <= start_frame_number:
        raise serializers.ValidationError("end_frame_number must be greater than start_frame_number")
