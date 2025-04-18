from rest_framework import serializers
from endoreg_db.models import EndoscopyProcessor


class EndoscopyProcessorSerializer(serializers.ModelSerializer):
    """Serializer for EndoscopyProcessor representation."""

    class Meta:
        model = EndoscopyProcessor
        fields = "__all__"