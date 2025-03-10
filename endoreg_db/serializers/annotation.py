from rest_framework import serializers
from endoreg_db.models import  BinaryClassificationAnnotationTask, ImageClassificationAnnotation



class BinaryClassificationAnnotationTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = BinaryClassificationAnnotationTask
        fields = '__all__'

class ImageClassificationAnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImageClassificationAnnotation
        fields = '__all__'
