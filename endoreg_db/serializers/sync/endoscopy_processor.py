from rest_framework import serializers
from endoreg_db.models.hardware import endoscopy_processor


class EndoscopyProcessorSerializer(serializers.ModelSerializer):
    """Serializer for EndoscopyProcessor representation."""

    class Meta:
        model = endoscopy_processor.EndoscopyProcessor
        fields = "__all__"