from rest_framework import serializers
from ..models import VideoSegmentationLabel

class VideoLabelSegmentsSerializer(serializers.ModelSerializer):
    video_file_id = serializers.IntegerField(read_only=True)
    label_id_read = serializers.IntegerField(read_only=True)

    class Meta:
        model = VideoSegmentationLabel
        fields = '__all__'