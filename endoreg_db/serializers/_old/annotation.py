from rest_framework import serializers
from endoreg_db.models import  (
    ImageClassificationAnnotation,
    VideoSegmentationAnnotation
)


class VideoSegmentationAnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoSegmentationAnnotation
        fields = '__all__'

class ImageClassificationAnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImageClassificationAnnotation
        fields = '__all__'
