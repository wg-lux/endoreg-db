from typing import TYPE_CHECKING
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from endoreg_db.models import (
    Label,
    VideoFile,
)

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from endoreg_db.serializers import LabelVideoSegmentSerializer


def _update(self:"LabelVideoSegmentSerializer", instance, validated_data):
    """
    Updates a LabelVideoSegment instance with new data.
    
    Handles updates to the associated video file, label (by ID or name), and segment boundaries (by time or frame number). Converts provided start and end times to frame numbers using the video's FPS. Raises a validation error if referenced video or label does not exist or if an error occurs during the update.
    
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
            fps_raw = getattr(video_file, 'fps', 30)
            try:
                if isinstance(fps_raw, str):
                    fps = float(fps_raw)
                elif isinstance(fps_raw, (int, float)):
                    fps = float(fps_raw)
                else:
                    fps = 30.0
            except (ValueError, TypeError):
                fps = 30.0
            if fps <= 0:
                fps = 30.0
            return fps

        # Convert time to frame numbers if provided
        if start_time is not None:
            fps = _get_valid_fps(instance.video_file)
            instance.start_frame_number = int(start_time * fps)
        
        if end_time is not None:
            fps = _get_valid_fps(instance.video_file)
            instance.end_frame_number = int(end_time * fps)
        
        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        logger.info("Updated video segment: %s", instance.pk)
        return instance
        
    except Exception as e:
        logger.error("Error updating video segment %s: %s", instance.pk, str(e))
        raise serializers.ValidationError(f"Failed to update segment: {str(e)}")

