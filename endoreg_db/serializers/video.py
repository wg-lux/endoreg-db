from rest_framework import serializers
from endoreg_db.models import Video, LegacyVideo, VideoImportMeta, LegacyLabelVideoSegment, LabelVideoSegment

class VideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Video
        fields = '__all__'

class LegacyVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegacyVideo
        fields = '__all__'

class VideoImportMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoImportMeta
        fields = '__all__'

class LabelVideoSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelVideoSegment
        fields = '__all__'

class LegacyLabelVideoSegmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegacyLabelVideoSegment
        fields = '__all__'
