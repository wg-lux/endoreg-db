from rest_framework import serializers
from typing import List
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from urllib.parse import urljoin
from pathlib import Path

from ...models import LabelVideoSegment, VideoFile
import logging
from ._lvs_create import (
    _create,
    _get_video_file,
    _get_label,
    _validate_fps,
    _calculate_frame_numbers,
    _get_information_source
)
from ._lvs_update import (
    _update,
)
from ._lvs_validate import (
    _validate,
    _extract_and_validate_basic_attrs,
    _process_time_data,
    _process_frame_data,
    _validate_time_and_frame_presence,
    _validate_time_constraints,
    _validate_frame_constraints
)
from endoreg_db.serializers import ImageClassificationAnnotationSerializer

logger = logging.getLogger(__name__)

# add these small helpers (could be private functions in this module)
def _media_relpath_from_file_path(file_path) -> str:
    """
    Return a media-relative path (never an absolute server path).
    If MEDIA_ROOT is a prefix, strip it; otherwise return the basename.
    Accepts str or Path-like.
    """
    p = Path(str(file_path))
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if media_root:
        try:
            rel = p.resolve().relative_to(Path(media_root).resolve())
            return rel.as_posix()
        except Exception:
            pass
    return p.name  # safe fallback

def _media_url_from_file_path(file_path, request=None) -> str:
    """
    Build a public URL for the file using MEDIA_URL + relpath.
    If `request` is provided, return an absolute URL.
    """
    base = getattr(settings, "MEDIA_URL", "/media/")
    if not base.endswith("/"):
        base += "/"
    rel = _media_relpath_from_file_path(file_path)
    url = urljoin(base, rel)
    if request is not None:
        try:
            return request.build_absolute_uri(url)
        except Exception:
            pass
    return url


class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    """Serializer for creating and retrieving LabelVideoSegment instances."""
    
    # Additional fields for convenience - matching frontend expectations
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()

    # Input fields (write_only for creation)
    video_id = serializers.IntegerField(required=False, help_text="Video file ID")
    label_id = serializers.IntegerField(required=False, allow_null=True, help_text="Label ID")
    
    # Add support for label names (both Label and VideoSegmentationLabel)
    label_name = serializers.CharField(write_only=True, required=False, allow_null=True, help_text="Label name")
    label_display = serializers.SerializerMethodField()
       
    # Read-only fields for response
    video_name = serializers.SerializerMethodField(read_only=True)
    frame_predictions = serializers.SerializerMethodField(read_only=True, help_text="Frame predictions for the video segment")
    manual_frame_annotations = serializers.SerializerMethodField(read_only=True, help_text="Manual frame annotations for the video segment")

    time_segments = serializers.SerializerMethodField(read_only=True, help_text="Time segments for the video segment")

    create = _create
    get_video_file = _get_video_file
    get_label = _get_label
    validate_fps = _validate_fps
    calculate_frame_numbers = _calculate_frame_numbers
    get_information_source = _get_information_source

    update = _update

    validate = _validate
    extract_and_validate_basic_attrs = _extract_and_validate_basic_attrs
    process_time_data = _process_time_data
    process_frame_data = _process_frame_data
    validate_time_and_frame_presence = _validate_time_and_frame_presence
    validate_time_constraints = _validate_time_constraints
    validate_frame_constraints = _validate_frame_constraints

    class Meta:
        model = LabelVideoSegment
        fields = [
            'id',
            "video_file",
            'video_name',
            "video_id",
            "label",
            'label_name',
            "label_id",
            'start_frame_number',
            'end_frame_number',
            'start_time',
            'end_time',
            "label_display", 
            "frame_predictions",
            "manual_frame_annotations",
            "time_segments"
        ]
        read_only_fields = ['id', 'video_name']
        extra_kwargs = {
            'start_frame_number': {'required': False},
            'end_frame_number': {'required': False},
            'video_file': {'required': False},
            'label': {'required': False},
        }
    

    def __init__(self, *args, **kwargs):
        """
        Initialize the serializer and log the initial input data if available.
        """
        super().__init__(*args, **kwargs)
        if hasattr(self, 'initial_data'):
            logger.debug(f"Serializer initialized with data: {self.initial_data}")


    def get_time_segments(self, obj: LabelVideoSegment) -> List[dict]:
        frames = obj.frames
        time_segments = {
            "segment_id": obj.id,
            "segment_start": obj.start_frame_number,
            "segment_end": obj.end_frame_number,
            "start_time": obj.start_time,
            "end_time": obj.end_time,
            "frames": []
        }

        request = self.context.get("request") if hasattr(self, "context") else None

        for frame in frames:
            all_classifications = ImageClassificationAnnotationSerializer(
                frame.image_classification_annotations.all(), many=True
            ).data
            predictions = ImageClassificationAnnotationSerializer(frame.predictions, many=True).data
            manual_annotations = ImageClassificationAnnotationSerializer(
                frame.manual_annotations, many=True
            ).data if frame.has_manual_annotations else []

            # 👇 changed here: no absolute server path; give a media-relative path + a URL
            rel = _media_relpath_from_file_path(frame.file_path)
            url = _media_url_from_file_path(frame.file_path, request=request)

            frame_data = {
                "frame_filename": Path(str(frame.file_path)).name,
                "frame_file_path": rel,            # backwards-compatible, now relative
                "frame_url": url,                  # new: what the frontend should use
                "all_classifications": all_classifications,
                "predictions": predictions,
                "frame_id": frame.id,
                "manual_annotations": manual_annotations
            }
            time_segments["frames"].append(frame_data)

        return time_segments

    def get_label_name(self, obj):# -> Any | Literal['unknown']:
        """
        Return the name of the label associated with the segment, or "unknown" if no label is set.
        """
        if obj.label:
            return obj.label.name
        return "unknown"
    
    def get_manual_frame_annotations(self, obj:LabelVideoSegment):
        """
        Return serialized manual frame annotations for the given video segment.
        
        Parameters:
            obj (LabelVideoSegment): The video segment instance whose manual frame annotations are to be serialized.
        
        Returns:
            list: A list of serialized manual frame annotation data.
        """
        return ImageClassificationAnnotationSerializer(obj.manual_frame_annotations, many=True).data
    
    def get_frame_predictions(self, obj:LabelVideoSegment) -> List[dict]:
        """
        Return serialized frame prediction annotations for the given video segment.
        
        Parameters:
            obj (LabelVideoSegment): The video segment instance whose frame predictions are to be serialized.
        
        Returns:
            List[dict]: A list of serialized frame prediction annotation data.
        """
        return ImageClassificationAnnotationSerializer(obj.frame_predictions, many=True).data
    
    def get_all_annotations(self, obj:LabelVideoSegment):
        """
        Retrieve all image classification annotations for every frame in the given video segment.
        
        Parameters:
            obj (LabelVideoSegment): The video segment instance whose frame annotations are to be retrieved.
        
        Returns:
            list: A list of serialized image classification annotations for all frames in the segment.
        """
        return ImageClassificationAnnotationSerializer(obj.all_frame_annotations, many=True).data

    def get_label_display(self, obj:LabelVideoSegment):
        """
        Return the German translation of the label name for display purposes.
        
        If the segment has no label, returns "Unbekannt". If a translation is not available, returns the original label name.
        """
        if not obj.label:
            return "Unbekannt"
            
        label_name = obj.label.name
        #TODO Refactor
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
        Return the display name of the video file associated with the segment.
        
        If the video file has an `original_file_name`, it is returned; otherwise, a fallback name using the video ID is provided. Returns "Unknown Video" if the video file is inaccessible.
        """
        try:
            video = obj.video_file
            return getattr(video, 'original_file_name', f'Video {video.id}')
        except (AttributeError, ObjectDoesNotExist):
            return 'Unknown Video'
 
    def get_start_time(self, obj:LabelVideoSegment):
        """
        Return the start time of the video segment in seconds.
        """
        return obj.start_time
    
    def get_end_time(self, obj):
        """
        Return the end time of the video segment in seconds.
        """
        return obj.end_time
    
    def is_valid(self, raise_exception=False):
        """
        Validates the serializer input data and logs the validation process.
        
        Parameters:
            raise_exception (bool): If True, raises a ValidationError on failure.
        
        Returns:
            bool: True if the data is valid, False otherwise.
        """
        logger.debug("Starting validation")
        result = super().is_valid(raise_exception=raise_exception)
        if not result:
            logger.debug(f"Validation errors: {self.errors}")
        return result

    def to_internal_value(self, data):
        """
        Normalizes input data by mapping between `video_id`/`label_id` and `video_file`/`label` keys to ensure consistent internal representation for model creation and validation.
        
        This allows the serializer to accept either set of keys and internally synchronize them before further processing.
        """
        #TODO @coderabbitai create an issue for @Hamzaukw 
        # we need to ensure consistent naming across serializers
        # Currently we use it in various places, but it should be consistent
        # Either make the usage consistent or create another serializer that handles this
        # Map video_file → video_id and label → label_id
        if 'video_file' in data:
            data['video_id'] = data['video_file']
        if 'label' in data:
            data['label_id'] = data['label']
        # Map video_id → video_file and label_id → label
        if 'video_id' in data:
            data['video_file'] = data['video_id']
        if 'label_id' in data:
            data['label'] = data['label_id']
        return super().to_internal_value(data)
    
    def to_representation(self, instance):
        """
        Return a JSON-compatible representation of a label video segment with calculated start and end times in seconds, label name, and explicit video and label IDs.
        
        Raises:
            ValueError: If the segment is not associated with a video file.
            TypeError: If the associated video file is not a VideoFile instance.
        """
        data = super().to_representation(instance)
        video_file = instance.video_file
        if video_file is None:
            raise ValueError("Video file must be associated with the segment")
        if not isinstance(video_file, VideoFile):
            raise TypeError("Expected video_file to be an instance of VideoFile")
        # Add calculated time fields for frontend compatibility
        data['start_time'] = video_file.frame_number_to_s(instance.start_frame_number)
        data['end_time'] = video_file.frame_number_to_s(instance.end_frame_number)
        # Ensure label_name is always present in response
        if instance.label:
            data['label_name'] = instance.label.name
        else:
            data['label_name'] = None
        # Explicitly add video_id and label_id to the output for frontend convenience
        data['video_id'] = instance.video_file.id if instance.video_file else None
        data['label_id'] = instance.label.id if instance.label else None
        return data
