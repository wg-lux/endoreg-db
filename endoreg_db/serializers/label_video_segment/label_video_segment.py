from rest_framework import serializers
from django.core.exceptions import ObjectDoesNotExist
from ...models import LabelVideoSegment, VideoFile, Label, InformationSource
import logging
from ._lvs_create import _create
from ._lvs_update import _update
from ._lvs_validate import _validate
from ._lvs_to_representation import _to_representation

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
    
    create = _create
    update = _update
    validate = _validate
    to_representation = _to_representation

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
