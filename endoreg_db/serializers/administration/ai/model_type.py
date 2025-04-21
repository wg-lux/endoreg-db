from rest_framework import serializers
from endoreg_db.models import ModelType

class ModelTypeSerializer(serializers.ModelSerializer):
    """
    Serializer for the ModelType model.
    """
    class Meta:
        model = ModelType
        fields = '__all__'
