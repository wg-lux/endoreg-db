from rest_framework import serializers
from endoreg_db.models import Video, VideoImportMeta, LabelVideoSegment, VideoMeta, Center, EndoscopyProcessor, Patient, PatientExamination

class VideoMetaSerializer(serializers.ModelSerializer):
    """Serializer for nested VideoMeta representation."""
    class Meta:
        model = VideoMeta
        # Include fields relevant for export, adjust as needed
        fields = "__all__"

class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = "__all__"


class VideoImportMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoImportMeta
        fields = "__all__"


class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelVideoSegment
        fields = "__all__"
