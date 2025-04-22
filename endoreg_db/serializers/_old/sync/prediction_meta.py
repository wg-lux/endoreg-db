from rest_framework import serializers
from endoreg_db.models import VideoPredictionMeta

class VideoPredictionMetaSerializer(serializers.Serializer):
    """Serializer for PredictionMeta representation."""

    class Meta:
        model = VideoPredictionMeta
        fields = []
