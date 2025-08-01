from endoreg_db.models import VideoImportMeta, VideoMeta


from rest_framework import serializers


class VideoMetaSerializer(serializers.ModelSerializer):
    """Serializer for nested VideoMeta representation."""
    class Meta:
        model = VideoMeta
        # Include fields relevant for export, adjust as needed
        fields = "__all__"


class VideoImportMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoImportMeta
        fields = "__all__"