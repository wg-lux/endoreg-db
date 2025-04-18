
from rest_framework import serializers
from endoreg_db.models import EndoscopyProcessor

class ExportEndoscopyProcessorSerializer(serializers.ModelSerializer):
    """Serializer for EndoscopyProcessor representation."""
    class Meta:
        model = EndoscopyProcessor
        fields = ['name', "name_de"]
