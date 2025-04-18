from rest_framework import serializers
from endoreg_db.models import Video

from .patient import PatientSerializer
from .sensitive_meta import SensitiveMetaSerializer
from .label_video_segment import LabelVideoSegmentSerializer


#######
PATIENT_FULL = True
SENSITIVE_META_FULL = True
LABEL_VIDEO_SEGMENTS = True
#######

class VideoSerializer(serializers.ModelSerializer):
    """Serializer for Video representation."""
    if PATIENT_FULL:
        patient = PatientSerializer()
    else:
        patient = serializers.PrimaryKeyRelatedField(
            queryset=Video.objects.all(),
            source="patient",
        )

    if SENSITIVE_META_FULL:
        sensitive_meta = SensitiveMetaSerializer()
    else:
        sensitive_meta = serializers.PrimaryKeyRelatedField(
            queryset=Video.objects.all(),
            source="sensitive_meta",
        )

    # Use CharField with source='file.name' to get the path relative to MEDIA_ROOT
    file = serializers.CharField(source='file.name', read_only=True)

    if LABEL_VIDEO_SEGMENTS:
        label_video_segments = LabelVideoSegmentSerializer(many=True, required=False)
    else:
        label_video_segments = serializers.PrimaryKeyRelatedField(
            queryset=Video.objects.all(),
            source="label_video_segments",
            many=True,
        )

    class Meta:
        model = Video
        fields = [
            "patient",
            "sensitive_meta",
            "file",
            "label_video_segments",
        ]