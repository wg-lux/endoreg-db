from __future__ import annotations

from endoreg_db.models.media.video.video_file import VideoFile


from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class VideoBriefSerializer(serializers.ModelSerializer[VideoFile]):
    class Meta(_ModelSerializerMeta):
        model = VideoFile  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "original_file_name",
            "sensitive_meta_id",
        ]  # for tables/overview
