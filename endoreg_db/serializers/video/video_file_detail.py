from rest_framework import serializers
from django.conf import settings
from pathlib import Path

from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.serializers.video.video_file_brief import VideoBriefSerializer
from ...utils.calc_duration_seconds import _calc_duration_vf

class VideoDetailSerializer(VideoBriefSerializer):
    # pull selected fields from SensitiveMeta (READ-ONLY) - using SerializerMethodField to handle datetime->date conversion
    patient_first_name = serializers.CharField(source="sensitive_meta.patient_first_name", read_only=True)
    patient_last_name  = serializers.CharField(source="sensitive_meta.patient_last_name",  read_only=True)
    patient_dob        = serializers.SerializerMethodField()
    examination_date   = serializers.SerializerMethodField()

    file        = serializers.SerializerMethodField()
    full_path   = serializers.SerializerMethodField()
    duration    = serializers.SerializerMethodField()
    video_url   = serializers.SerializerMethodField()

    class Meta(VideoBriefSerializer.Meta):
        fields = VideoBriefSerializer.Meta.fields + [
            "file", "full_path", "video_url",
            "patient_first_name", "patient_last_name",
            "patient_dob", "examination_date",
            "duration",
        ]

    # ---------- helpers ---------- #
    def get_file(self, obj):
        f = obj.processed_file or obj.raw_file
        return f.name if f else None

    def get_full_path(self, obj):
        f = obj.processed_file or obj.raw_file
        return str(Path(settings.MEDIA_ROOT) / f.name) if f else None

    def get_video_url(self, obj):
        request = self.context.get("request")
        #TODO remove hardcoded path here 
        return request.build_absolute_uri(f"/api/media/videos/{obj.pk}/") if request else None
    
    def get_duration(self, obj:VideoFile):
        return obj.duration or _calc_duration_vf(obj)
    
    def get_patient_dob(self, obj):
        """Extract date from datetime field, handling timezone properly."""
        if obj.sensitive_meta and obj.sensitive_meta.patient_dob:
            # If it's a datetime, extract the date part
            dob = obj.sensitive_meta.patient_dob
            return dob.date() if hasattr(dob, 'date') else dob
        return None
    
    def get_examination_date(self, obj):
        """Extract date from datetime field, handling timezone properly."""
        if obj.sensitive_meta and obj.sensitive_meta.examination_date:
            # If it's a datetime, extract the date part
            exam_date = obj.sensitive_meta.examination_date
            return exam_date.date() if hasattr(exam_date, 'date') else exam_date
        return None
