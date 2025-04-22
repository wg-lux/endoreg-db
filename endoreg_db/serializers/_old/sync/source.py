from rest_framework import serializers
from endoreg_db.models import InformationSource

class InformationSourceSerializer(serializers.ModelSerializer):
    """Serializer for InformationSource representation."""

    class Meta:
        model = InformationSource
        fields = ['name', "name_de"]
