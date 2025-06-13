from rest_framework import serializers
from .models import VideoLabelSegments

class VideoLabelSegmentsSerializer(serializers.ModelSerializer):
    video_file_id = serializers.IntegerField(read_only=True)
    label_id_read = serializers.IntegerField(read_only=True)

    class Meta:
        model = VideoLabelSegments
        fields = '__all__'