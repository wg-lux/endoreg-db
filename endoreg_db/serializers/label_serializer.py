from rest_framework import serializers
from django.core.exceptions import ObjectDoesNotExist
from ..models import LabelVideoSegment, VideoFile, Label, InformationSource, VideoSegmentationLabel
import logging

logger = logging.getLogger(__name__)


class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    """Serializer for creating and retrieving LabelVideoSegment instances."""
    
    # Additional fields for convenience - matching frontend expectations
    start_time = serializers.FloatField(required=False, help_text="Start time in seconds")
    end_time = serializers.FloatField(required=False, help_text="End time in seconds")
    
    # Input fields (write_only for creation)
    video_id = serializers.IntegerField(write_only=True, required=True, help_text="Video file ID")
    label_id = serializers.IntegerField(write_only=True, required=False, allow_null=True, help_text="Label ID")
    
    # Add support for label names (both Label and VideoSegmentationLabel)
    label_name = serializers.CharField(required=False, allow_null=True, help_text="Label name (supports both Label and VideoSegmentationLabel names)")
    
    # Read-only fields for response
    video_name = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = LabelVideoSegment
        fields = [
            'id',
            'start_frame_number',
            'end_frame_number',
            'start_time',
            'end_time',
            'video_id',
            'label_id',
            'label_name',
            'video_name',
        ]
        read_only_fields = ['id', 'video_name']
        extra_kwargs = {
            'start_frame_number': {'required': False},
            'end_frame_number': {'required': False},
        }
    
    def get_video_name(self, obj):
        """
        Returns the display name of the associated video file.
        
        If the video file has an `original_file_name`, it is returned; otherwise, a fallback name using the video ID is provided. Returns 'Unknown Video' if the video file is inaccessible.
        """
        try:
            video = obj.video_file
            return getattr(video, 'original_file_name', f'Video {video.id}')
        except (AttributeError, ObjectDoesNotExist):
            return 'Unknown Video'
    
    def get_or_create_label_from_name(self, label_name):
        """
        Retrieves or creates a Label instance based on the provided label name.
        
        If a Label with the given name exists, it is returned. If not, attempts to find a VideoSegmentationLabel with the same name and creates a new Label using its details. If neither exists, creates a new Label with a manual creation description. Returns None if no label name is provided.
        """
        if not label_name:
            return None
            
        # First, try to find an existing Label with this name
        try:
            label = Label.objects.get(name=label_name)
            logger.info("Found existing Label: %s", label_name)
            return label
        except ObjectDoesNotExist:
            pass
        
        # Next, try to find a VideoSegmentationLabel and create corresponding Label
        try:
            video_seg_label = VideoSegmentationLabel.objects.get(name=label_name)
            
            # Create a new Label based on the VideoSegmentationLabel
            label = Label.objects.create(
                name=video_seg_label.name,
                description=getattr(video_seg_label, 'description', f'Label created from VideoSegmentationLabel: {video_seg_label.name}')
            )
            logger.info("Created new Label '%s' from VideoSegmentationLabel", label_name)
            return label
            
        except ObjectDoesNotExist:
            pass
        
        # If neither exists, create a new Label
        label = Label.objects.create(
            name=label_name,
            description=f'Manually created label: {label_name}'
        )
        logger.info("Created new Label: %s", label_name)
        return label
    
    def validate(self, attrs):
        """
        Validates input data for a video segment, ensuring either time or frame information is provided and logically consistent.
        
        Checks that either start/end times or start/end frame numbers are present, and that start values are non-negative and end values are greater than start values. Allows segments without labels.
        """
        # Check if we have either time or frame data
        has_time_data = 'start_time' in attrs and 'end_time' in attrs
        has_frame_data = 'start_frame_number' in attrs and 'end_frame_number' in attrs
        
        if not has_time_data and not has_frame_data:
            raise serializers.ValidationError(
                "Either start_time/end_time or start_frame_number/end_frame_number must be provided"
            )
        
        # Validate that we have either label_id or label_name
        label_id = attrs.get('label_id')
        label_name = attrs.get('label_name')
        
        if not label_id and not label_name:
            # Allow null labels for segments without specific labels
            logger.info("Creating segment without label")
        
        # Validate time data if provided
        if has_time_data:
            if attrs['start_time'] < 0:
                raise serializers.ValidationError("start_time must be non-negative")
            if attrs['end_time'] <= attrs['start_time']:
                raise serializers.ValidationError("end_time must be greater than start_time")
        
        # Validate frame data if provided
        if has_frame_data:
            if attrs['start_frame_number'] < 0:
                raise serializers.ValidationError("start_frame_number must be non-negative")
            if attrs['end_frame_number'] <= attrs['start_frame_number']:
                raise serializers.ValidationError("end_frame_number must be greater than start_frame_number")
        
        return attrs
    
    def create(self, validated_data):
        """
        Creates a new LabelVideoSegment instance using validated input data.
        
        Associates the segment with a specified video file and label, supporting label assignment by ID or by name (creating a new label if necessary). Converts provided start and end times to frame numbers based on the video's FPS if frame numbers are not directly supplied. Ensures required frame data is present and links the segment to a default manual annotation information source. Raises a validation error if referenced video or label does not exist, or if frame data cannot be determined.
        
        Returns:
            The created LabelVideoSegment instance.
        """
        try:
            # Extract convenience fields
            video_id = validated_data.pop('video_id')
            label_id = validated_data.pop('label_id', None)
            label_name = validated_data.pop('label_name', None)
            start_time = validated_data.pop('start_time', None)
            end_time = validated_data.pop('end_time', None)
            
            # Get the video file
            try:
                video_file = VideoFile.objects.get(id=video_id)
            except ObjectDoesNotExist as exc:
                raise serializers.ValidationError(f"VideoFile with id {video_id} does not exist") from exc
            
            # Determine the label to use
            label = None
            if label_id:
                try:
                    label = Label.objects.get(id=label_id)
                except ObjectDoesNotExist as exc:
                    raise serializers.ValidationError(f"Label with id {label_id} does not exist") from exc
            elif label_name:
                label = self.get_or_create_label_from_name(label_name)
            
            # Calculate frame numbers from time if provided and not already set
            # Fix: Ensure fps is properly converted to numeric type
            fps_raw = getattr(video_file, 'fps', 30)  # Default to 30 fps if not available
            
            try:
                if isinstance(fps_raw, str):
                    fps = float(fps_raw)
                elif isinstance(fps_raw, (int, float)):
                    fps = float(fps_raw)
                else:
                    fps = 30.0  # Default fallback
            except (ValueError, TypeError):
                fps = 30.0  # Default fallback if conversion fails
            
            # Ensure fps is positive
            if fps <= 0:
                fps = 30.0
            
            if start_time is not None and 'start_frame_number' not in validated_data:
                validated_data['start_frame_number'] = int(start_time * fps)
            
            if end_time is not None and 'end_frame_number' not in validated_data:
                validated_data['end_frame_number'] = int(end_time * fps)
            
            # Ensure we have frame numbers
            if 'start_frame_number' not in validated_data or 'end_frame_number' not in validated_data:
                raise serializers.ValidationError("Could not determine frame numbers from provided data")
            
            # Get or create a default information source for manual annotations
            source, _ = InformationSource.objects.get_or_create(
                name='Manual Annotation',
                defaults={
                    'description': 'Manually created label segments via web interface',
                }
            )
            
            # Create the segment directly
            segment = LabelVideoSegment(
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
            
        except Exception as e:
            logger.error("Error creating video segment: %s", str(e))
            raise serializers.ValidationError(f"Failed to create segment: {str(e)}")
    
    def update(self, instance, validated_data):
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
            if video_id and video_id != instance.video_file.id:
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
            
            # Convert time to frame numbers if provided
            if start_time is not None:
                fps_raw = getattr(instance.video_file, 'fps', 30)
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
                instance.start_frame_number = int(start_time * fps)
            
            if end_time is not None:
                fps_raw = getattr(instance.video_file, 'fps', 30)
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
    
    def to_representation(self, instance):
        """
        Returns a JSON-compatible representation of the label video segment, including calculated start and end times in seconds and the label name.
        
        Adds `start_time` and `end_time` fields by converting frame numbers to seconds using the video's FPS, defaulting to zero if unavailable. Ensures `label_name` is always included in the output.
        """
        data = super().to_representation(instance)
        
        # Add calculated time fields for frontend compatibility
        try:
            fps_raw = getattr(instance.video_file, 'fps', 30)
            
            # Robust FPS conversion matching create/update methods
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
            
            data['start_time'] = instance.start_frame_number / fps
            data['end_time'] = instance.end_frame_number / fps
        except (AttributeError, ZeroDivisionError):
            data['start_time'] = 0
            data['end_time'] = 0
        
        # Ensure label_name is always present in response
        if instance.label:
            data['label_name'] = instance.label.name
        else:
            data['label_name'] = None
        
        return data