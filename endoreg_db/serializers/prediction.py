from rest_framework import serializers
from endoreg_db.models import (
    ImageClassificationPrediction,
    LegacyVideoPredictionMeta
)

class ImageClassificationPredictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImageClassificationPrediction
        fields = '__all__'

class LegacyVideoPredictionMetaSerializer(serializers.ModelSerializer):
    class Meta:
        model = LegacyVideoPredictionMeta
        fields = '__all__'
