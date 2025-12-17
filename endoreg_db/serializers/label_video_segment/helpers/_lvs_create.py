from typing import TYPE_CHECKING
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from endoreg_db.models import (
    Label,
    VideoFile,
    LabelVideoSegment,
    InformationSource
)

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer

def _get_video_file(self, video_id):
    """
    Retrieve a VideoFile instance by its ID.
    
    Raises a serializers.ValidationError if the VideoFile does not exist.
    """
    try:
        return VideoFile.objects.get(id=video_id)
    except ObjectDoesNotExist as exc:
        raise serializers.ValidationError(f"VideoFile with id {video_id} does not exist") from exc

def _get_label(self, label_id, label_name):
    """
    Retrieve a Label instance by ID or name, creating it if necessary.
    
    Raises a ValidationError if the label cannot be found, created, or if neither identifier is provided.
    """
    if label_id:
        try:
            return Label.objects.get(id=label_id)
        except ObjectDoesNotExist as exc:
            raise serializers.ValidationError(f"Label with id {label_id} does not exist") from exc
    elif label_name:
        label, _ = Label.get_or_create_from_name(label_name)
        if not label:
            raise serializers.ValidationError(f"Failed to create or retrieve label with name {label_name}")
        return label
    else:
        raise serializers.ValidationError("Either label_id or label_name must be provided")

def _validate_fps(self, video_file):
    """
    Validate and return the frames per second (FPS) value from a video file.
    
    Raises a validation error if the FPS is missing, not a positive number, or cannot be converted to a float.
    
    Returns:
        float: The validated FPS value.
    """
    fps_raw = video_file.get_fps()
    if fps_raw is None:
        raise serializers.ValidationError("Video file must have a defined FPS")
    try:
        fps = float(fps_raw)
        if fps <= 0:
            raise ValueError("FPS must be a positive number")
    except (ValueError, TypeError):
        raise serializers.ValidationError("Invalid FPS format in video file")
    return fps

def _calculate_frame_numbers(self, validated_data, fps):
    """
    Convert start and end times in the input data to corresponding frame numbers using the provided FPS value.
    
    Parameters:
        validated_data (dict): Input data containing 'start_time' and 'end_time' keys.
        fps (float): Frames per second of the video.
    
    Returns:
        dict: The input data dictionary updated with 'start_frame_number' and 'end_frame_number' keys.
    
    Raises:
        serializers.ValidationError: If frame numbers cannot be determined from the provided data.
    """
    if validated_data.get('start_time') is not None:
        validated_data['start_frame_number'] = int(float(validated_data['start_time']) * fps)
    if validated_data.get('end_time') is not None:
        validated_data['end_frame_number'] = int(float(validated_data['end_time']) * fps)
    if 'start_frame_number' not in validated_data or 'end_frame_number' not in validated_data:
        raise serializers.ValidationError("Could not determine frame numbers from provided data")
    return validated_data

def _get_information_source(self):
    """
    Retrieve or create the information source for manual annotation.
    
    Returns:
        InformationSource: The source object representing manual annotation.
    """
    source, _ = InformationSource.objects.get_or_create(
        name='Manual Annotation',
        defaults={
            'description': 'Manually created label segments via web interface',
        }
    )
    return source

def _create(self:"LabelVideoSegmentSerializer", validated_data):
    """
    Create and persist a new LabelVideoSegment instance from validated input data.
    
    This method extracts relevant fields from the input, retrieves or creates related objects (video file, label, information source), validates video FPS, calculates frame numbers from time values, and creates a new LabelVideoSegment. Raises a validation error if required data is missing or invalid, or if creation fails.
    
    Returns:
        LabelVideoSegment: The newly created and saved segment instance.
    """
    try:
        # Extract convenience fields
        video_id = validated_data.pop('video_id')
        label_id = validated_data.pop('label_id', None)
        label_name = validated_data.pop('label_name', None)
        start_time = validated_data.pop('start_time', None)
        end_time = validated_data.pop('end_time', None)

        video_file = self.get_video_file(video_id)
        label = self.get_label(label_id, label_name)
        fps = self.validate_fps(video_file)
        validated_data['start_time'] = start_time
        validated_data['end_time'] = end_time
        validated_data = self.calculate_frame_numbers(validated_data, fps)
        source = self.get_information_source()

        # Create the segment directly
        segment = LabelVideoSegment.safe_create(
            video_file=video_file,
            label=label,
            source=source,
            start_frame_number=validated_data['start_frame_number'],
            end_frame_number=validated_data['end_frame_number'],
            prediction_meta=None  # No prediction meta for manual annotations
        )
        segment.save()

        logger.info("Created new video segment: %s for video %s with label %s",
                    segment.pk, video_id, label.name if label else 'None')
        return segment

    except serializers.ValidationError as e:
        logger.error("Validation error while creating video segment: %s", str(e))
        raise
    except Exception as e:
        logger.error("Unexpected error while creating video segment: %s", str(e))
        raise serializers.ValidationError(f"Failed to create segment: {str(e)}")
