from rest_framework import serializers
from endoreg_db.models import VideoFile, VideoImportMeta, LabelVideoSegment, VideoMeta, SensitiveMeta
# serializers/video.py
from pathlib import Path
from rest_framework import serializers
from django.conf import settings

from .utils import calc_duration_seconds         # wrap your OpenCV helper


class VideoBriefSer(serializers.ModelSerializer):
    class Meta:
        model  = VideoFile
        fields = ["id", "original_file_name", "sensitive_meta_id"]


class VideoDetailSer(VideoBriefSer):
    patient_first_name = serializers.CharField(source="sensitive_meta.patient_first_name", read_only=True)
    patient_last_name  = serializers.CharField(source="sensitive_meta.patient_last_name",  read_only=True)
    patient_dob        = serializers.DateField (source="sensitive_meta.patient_dob",        read_only=True)
    examination_date   = serializers.DateField (source="sensitive_meta.examination_date",   read_only=True)

    file       = serializers.SerializerMethodField()
    full_path  = serializers.SerializerMethodField()
    duration   = serializers.SerializerMethodField()
    video_url  = serializers.SerializerMethodField()

    class Meta(VideoBriefSer.Meta):
        fields = VideoBriefSer.Meta.fields + [
            "file", "full_path", "video_url",
            "patient_first_name", "patient_last_name",
            "patient_dob", "examination_date",
            "duration",
        ]

    # helpers ----------------------------------------------------------
    def get_file(self, obj):
        f = obj.processed_file or obj.raw_file
        return f.name if f else None

    def get_full_path(self, obj):
        f = obj.processed_file or obj.raw_file
        return str(Path(settings.MEDIA_ROOT) / f.name) if f else None

    def get_video_url(self, obj):
        req = self.context.get("request")
        return req.build_absolute_uri(f"/api/media/videos/{obj.pk}/") if req else None

    def get_duration(self, obj):
        return obj.duration or calc_duration_seconds(obj)


class SensitiveMetaUpdateSer(serializers.ModelSerializer):
    sensitive_meta_id = serializers.IntegerField(write_only=True)

    class Meta:
        model  = SensitiveMeta
        fields = ["sensitive_meta_id", "patient_first_name",
                  "patient_last_name", "patient_dob", "examination_date"]


class VideoImportMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoImportMeta
        fields = "__all__"


class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelVideoSegment
        fields = "__all__"
