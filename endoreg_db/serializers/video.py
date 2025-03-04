from rest_framework import serializers
from endoreg_db.models import Video, VideoImportMeta, LabelVideoSegment


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
