from rest_framework import serializers
from endoreg_db.models import (
    Endoscope,
    EndoscopeType,
    EndoscopyProcessor,
)

class EndoscopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Endoscope
        fields = '__all__'

class EndoscopyProcessorSerializer(serializers.ModelSerializer):
    class Meta:
        model = EndoscopyProcessor
        fields = '__all__'

class EndoscopeTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EndoscopeType
        fields = '__all__'