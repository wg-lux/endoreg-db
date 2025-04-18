from rest_framework import serializers
from endoreg_db.models import Video, Patient, SensitiveMeta, LabelVideoSegment, VideoMeta

from .patient import PatientSerializer
from .sensitive_meta import SensitiveMetaSerializer
from .label_video_segment import LabelVideoSegmentSerializer
from .video_meta import VideoMetaSerializer

#######
PATIENT_FULL = True
SENSITIVE_META_FULL = True
LABEL_VIDEO_SEGMENTS = True
VIDEO_META_FULL = True
#######

class VideoSerializer(serializers.ModelSerializer):
    """Serializer for Video representation."""
    patient = serializers.PrimaryKeyRelatedField(queryset=Patient.objects.all())
    sensitive_meta = serializers.PrimaryKeyRelatedField(queryset=SensitiveMeta.objects.all())
    file = serializers.CharField(source='file.name', read_only=True)
    label_video_segments = serializers.PrimaryKeyRelatedField(
        queryset=LabelVideoSegment.objects.all(), many=True, required=False
    )
    video_meta = serializers.PrimaryKeyRelatedField(queryset=VideoMeta.objects.all())

    def __init__(self, *args, **kwargs):
        patient_full = kwargs.pop("patient_full", PATIENT_FULL)
        sensitive_meta_full = kwargs.pop("sensitive_meta_full", SENSITIVE_META_FULL)
        label_video_segments_full = kwargs.pop("label_video_segments_full", LABEL_VIDEO_SEGMENTS)
        video_meta_full = kwargs.pop("video_meta_full", VIDEO_META_FULL)
        endoscopy_processor_full = kwargs.pop("endoscopy_processor_full", True)

        super().__init__(*args, **kwargs)

        if patient_full:
            self.fields['patient'] = PatientSerializer()

        if sensitive_meta_full:
            self.fields['sensitive_meta'] = SensitiveMetaSerializer()

        if label_video_segments_full:
            self.fields['label_video_segments'] = LabelVideoSegmentSerializer(many=True, required=False)

        if video_meta_full:
            self.fields['video_meta'] = VideoMetaSerializer(
                endoscopy_processor_full=endoscopy_processor_full
            )

    class Meta:
        model = Video
        fields = [
            "patient",
            "sensitive_meta",
            "file",
            "label_video_segments",
            "video_meta"
        ]
