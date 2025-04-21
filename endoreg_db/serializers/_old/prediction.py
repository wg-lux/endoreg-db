from rest_framework import serializers
from endoreg_db.models import (
    ImageClassificationPrediction,
)


class ImageClassificationPredictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImageClassificationPrediction
        fields = "__all__"
