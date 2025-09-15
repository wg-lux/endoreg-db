from pathlib import Path
from rest_framework import serializers
from endoreg_db.models import VideoMeta, VideoImportMeta


class VideoMetaSerializer(serializers.ModelSerializer):
    fps = serializers.FloatField(read_only=True, allow_null=True)
    duration = serializers.FloatField(read_only=True, allow_null=True)
    width = serializers.IntegerField(read_only=True, allow_null=True)
    height = serializers.IntegerField(read_only=True, allow_null=True)
    frame_count = serializers.IntegerField(read_only=True, allow_null=True)
    created_at = serializers.SerializerMethodField()

    def get_created_at(self, obj):
        # returns created_at if your model has it, else None
        return getattr(obj, "created_at", None)

    class Meta:
        model = VideoMeta
        fields = (
            "id",
            "fps",
            "duration",
            "width",
            "height",
            "frame_count",
            "created_at",
        )


class VideoImportMetaSerializer(serializers.ModelSerializer):
    file_name = serializers.SerializerMethodField()

    def get_file_name(self, obj):
        return Path(obj.file_name).name if obj.file_name else None

    class Meta:
        model = VideoImportMeta
        fields = ("id", "file_name", "video_anonymized")
