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
    try:
        return VideoFile.objects.get(id=video_id)
    except ObjectDoesNotExist as exc:
        raise serializers.ValidationError(f"VideoFile with id {video_id} does not exist") from exc

def _get_label(self, label_id, label_name):
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
    if validated_data.get('start_time') is not None:
        validated_data['start_frame_number'] = int(float(validated_data['start_time']) * fps)
    if validated_data.get('end_time') is not None:
        validated_data['end_frame_number'] = int(float(validated_data['end_time']) * fps)
    if 'start_frame_number' not in validated_data or 'end_frame_number' not in validated_data:
        raise serializers.ValidationError("Could not determine frame numbers from provided data")
    return validated_data

def _get_information_source(self):
    source, _ = InformationSource.objects.get_or_create(
        name='Manual Annotation',
        defaults={
            'description': 'Manually created label segments via web interface',
        }
    )
    return source

def _create(self:"LabelVideoSegmentSerializer", validated_data):
    """
    Creates a new LabelVideoSegment instance using validated input data.
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
