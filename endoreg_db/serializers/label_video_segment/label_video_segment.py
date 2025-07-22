from rest_framework import serializers
from django.core.exceptions import ObjectDoesNotExist
from ...models import LabelVideoSegment, VideoFile, Label, InformationSource
import logging

logger = logging.getLogger(__name__)

class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    """Serializer for creating and retrieving LabelVideoSegment instances."""
    
    # Additional fields for convenience - matching frontend expectations
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()

    # Input fields (write_only for creation)
    video_id = serializers.IntegerField(write_only=True, required=True, help_text="Video file ID")
    label_id = serializers.IntegerField(write_only=True, required=False, allow_null=True, help_text="Label ID")
    
    # Add support for label names (both Label and VideoSegmentationLabel)
    label_name = serializers.CharField(write_only=True, required=False, allow_null=True, help_text="Label name")
    label_display = serializers.SerializerMethodField()
       
    # Read-only fields for response
    video_name = serializers.SerializerMethodField(read_only=True)
    
    class Meta:
        model = LabelVideoSegment
        fields = [
            'id',
            "video_file",  # Changed from video_id to video_file
            "label",       # Changed from label_id to label
            'start_frame_number',
            'end_frame_number',
            'start_time',
            'end_time',
            'video_id',
            'label_id',
            'label_name',
            'video_name',
            "label_display", 
        ]
        read_only_fields = ['id', 'video_name']
        extra_kwargs = {
            'start_frame_number': {'required': False},
            'end_frame_number': {'required': False},
            'video_file': {'required': False},  # Make video_file optional
        }
    

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if hasattr(self, 'initial_data'):
            logger.debug(f"Serializer initialized with data: {self.initial_data}")

    def validate(self, attrs):
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
    
    def create(self, validated_data):
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
                label, _ = Label.get_or_create_from_name(label_name)
                if not label:
                    raise serializers.ValidationError(f"Failed to create or retrieve label with name {label_name}")
            else:
                raise serializers.ValidationError("Either label_id or label_name must be provided")

            # Get FPS from the video file
            fps_raw = video_file.get_fps()
            if fps_raw is None:
                raise serializers.ValidationError("Video file must have a defined FPS")

            try:
                fps = float(fps_raw)
                if fps <= 0:
                    raise ValueError("FPS must be a positive number")
            except (ValueError, TypeError):
                raise serializers.ValidationError("Invalid FPS format in video file")

            # Convert start_time and end_time to frame numbers if provided
            if start_time is not None:
                validated_data['start_frame_number'] = int(float(start_time) * fps)
            if end_time is not None:
                validated_data['end_frame_number'] = int(float(end_time) * fps)

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

        except serializers.ValidationError as e:
            logger.error("Validation error while creating video segment: %s", str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error while creating video segment: %s", str(e))
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
    
    def get_label_name(self, obj):
        """Get the actual label name from the related Label model"""
        if obj.label:
            return obj.label.name
        return "unknown"
    
    def get_label_display(self, obj):
        """Get the German translation for display"""
        if not obj.label:
            return "Unbekannt"
            
        label_name = obj.label.name
        translations = {
            'appendix': 'Appendix',
            'blood': 'Blut',
            'diverticule': 'Divertikel',
            'grasper': 'Greifer',
            'ileocaecalvalve': 'Ileozäkalklappe',
            'ileum': 'Ileum',
            'low_quality': 'Niedrige Bildqualität',
            'nbi': 'Narrow Band Imaging',
            'needle': 'Nadel',
            'outside': 'Außerhalb',
            'polyp': 'Polyp',
            'snare': 'Snare',
            'water_jet': 'Wasserstrahl',
            'wound': 'Wunde'
        }
        return translations.get(label_name, label_name)
    
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
 
    def get_start_time(self, obj:LabelVideoSegment):
        """Convert start frame to time in seconds"""
        return obj.start_time
    
    def get_end_time(self, obj):
        """Convert end frame to time in seconds"""
        return obj.end_time
    
    def is_valid(self, raise_exception=False):
        logger.debug("Starting validation")
        result = super().is_valid(raise_exception=raise_exception)
        if not result:
            logger.debug(f"Validation errors: {self.errors}")
        return result
    
    def to_internal_value(self, data):
        """
        Accept both video_id/label_id and video_file/label for input data.
        """
        # Map video_file to video_id if present
        if 'video_file' in data and 'video_id' not in data:
            data['video_id'] = data['video_file']
        # Map label to label_id if present
        if 'label' in data and 'label_id' not in data:
            data['label_id'] = data['label']
        return super().to_internal_value(data)
