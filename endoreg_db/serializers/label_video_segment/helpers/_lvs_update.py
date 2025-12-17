from typing import TYPE_CHECKING, Dict, Any, Optional
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from endoreg_db.models import (
    Label,
    VideoFile,
    LabelVideoSegment
)

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer


def _validate_fps(video: "VideoFile"):
    """Raises a ValidationError if the video's FPS is invalid."""
    fps = video.get_fps()
    if not fps or fps <= 0:
        raise serializers.ValidationError("The video must have a valid FPS to convert times to frames.")
    return fps

def _convert_time_to_frame(time_value: float, fps: float) -> int:
    """Converts a time value in seconds to a frame number."""
    return int(time_value * fps)

def _update_frames_from_data(instance: "LabelVideoSegment", validated_data: Dict[str, Any], fps: Optional[float]):
    """Updates start and end frames from either time or frame data."""
    if 'start_time' in validated_data:
        if fps is None:
            fps = _validate_fps(instance.video)
        instance.start_frame_number = _convert_time_to_frame(validated_data['start_time'], fps)
    elif 'start_frame' in validated_data:
        instance.start_frame_number = validated_data['start_frame']

    if 'end_time' in validated_data:
        if fps is None:
            fps = _validate_fps(instance.video)
        instance.end_frame_number = _convert_time_to_frame(validated_data['end_time'], fps)
    elif 'end_frame' in validated_data:
        instance.end_frame_number = validated_data['end_frame']

def _update(instance: "LabelVideoSegment", validated_data: Dict[str, Any]) -> "LabelVideoSegment":
    """
    Handles the update logic for a LabelVideoSegment instance.
    This function is designed to be used within a serializer's update method.
    """
    video: Optional["VideoFile"] = validated_data.get('video')
    fps = None

    if video and video != instance.video:
        instance.video = video
        fps = _validate_fps(video)  # Validate FPS for the new video

    if 'label' in validated_data:
        instance.label = validated_data['label']

    _update_frames_from_data(instance, validated_data, fps)

    # Final validation of frame numbers
    if instance.start_frame_number >= instance.end_frame_number:
        raise serializers.ValidationError("start_frame must be less than end_frame.")
    
    if instance.video and instance.end_frame_number > instance.video.frame_count:
        raise serializers.ValidationError("end_frame cannot exceed the total frames of the video.")

    instance.save()
    return instance

def _update(self:"LabelVideoSegmentSerializer", instance, validated_data):
    """
    Update a LabelVideoSegment instance with new validated data.
    
    This method updates the associated video file, label (by ID or name), and segment boundaries (start and end times converted to frame numbers based on the video's FPS). If the referenced video or label does not exist, or if any error occurs during the update, a validation error is raised.
    
    Returns:
        The updated LabelVideoSegment instance.
    """
    try:
        # Handle time-based updates
        start_time = validated_data.pop('start_time', None)
        end_time = validated_data.pop('end_time', None)
        video_id = validated_data.pop('video_id', None)
        label_id = validated_data.pop('label_id', None)
        label_name = validated_data.pop('label_name', None)
        
        # Update video if provided
        if video_id and (not instance.video_file or video_id != instance.video_file.id):
            try:
                instance.video_file = VideoFile.objects.get(id=video_id)
            except ObjectDoesNotExist as exc:
                raise serializers.ValidationError(f"VideoFile with id {video_id} does not exist") from exc
        
        # Update label if provided
        if label_id is not None:
            if label_id:
                try:
                    instance.label = Label.objects.get(id=label_id)
                except ObjectDoesNotExist as exc:
                    raise serializers.ValidationError(f"Label with id {label_id} does not exist") from exc
            else:
                instance.label = None
        elif label_name is not None:
            if label_name:
                instance.label = self.get_or_create_label_from_name(label_name)
            else:
                instance.label = None
        
        def _get_valid_fps(video_file):
            """
            Extract and validate the frames-per-second (FPS) value from a video file object.
            
            Returns:
                float: The FPS value as a positive float. Defaults to 30.0 if missing, invalid, or non-positive.
            """

        # Convert time to frame numbers if provided
        if start_time is not None:
            fps = _validate_fps(instance.video_file)
            instance.start_frame_number = _convert_time_to_frame(start_time, fps)
        
        if end_time is not None:
            fps = _validate_fps(instance.video_file)
            instance.end_frame_number = _convert_time_to_frame(end_time, fps)
        
        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        logger.info("Updated video segment: %s", instance.pk)
        return instance
        
    except Exception as e:
        logger.error("Error updating video segment %s: %s", instance.pk, str(e))
        raise serializers.ValidationError(f"Failed to update segment: {str(e)}")

