from rest_framework import serializers
from endoreg_db.models import LegacyBinaryClassificationAnnotationTask, BinaryClassificationAnnotationTask, ImageClassificationAnnotation

class LegacyBinaryClassificationAnnotationTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegacyBinaryClassificationAnnotationTask
        fields = '__all__'

class BinaryClassificationAnnotationTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = BinaryClassificationAnnotationTask
        fields = '__all__'

class ImageClassificationAnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImageClassificationAnnotation
        fields = '__all__'
