from pathlib import Path
from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object
from endoreg_db.models.metadata.video_meta import VideoImportMeta, VideoMeta


class VideoMetaSerializer(serializers.ModelSerializer[VideoMeta]):
    fps = serializers.FloatField(read_only=True, allow_null=True)
    duration = serializers.FloatField(read_only=True, allow_null=True)
    width = serializers.IntegerField(read_only=True, allow_null=True)
    height = serializers.IntegerField(read_only=True, allow_null=True)
    frame_count = serializers.IntegerField(read_only=True, allow_null=True)
    created_at = serializers.SerializerMethodField()

    def get_created_at(self, obj: VideoMeta) -> object:
        # returns created_at if your model has it, else None
        return getattr(obj, "created_at", None)

    class Meta(_ModelSerializerMeta):
        model = VideoMeta  # pyright: ignore[reportAssignmentType]
        fields = (
            "id",
            "fps",
            "duration",
            "width",
            "height",
            "frame_count",
            "created_at",
        )


class VideoImportMetaSerializer(serializers.ModelSerializer[VideoImportMeta]):
    file_name = serializers.SerializerMethodField()

    def get_file_name(self, obj: VideoImportMeta) -> str | None:
        return Path(obj.file_name).name if obj.file_name else None

    class Meta(_ModelSerializerMeta):
        model = VideoImportMeta  # pyright: ignore[reportAssignmentType]
        fields = ("id", "file_name", "video_anonymized")
