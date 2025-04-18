from rest_framework import serializers
from endoreg_db.models import ExaminationType

class ExaminationTypesSerializer(serializers.ModelSerializer):
    """Serializer for ExaminationType representation."""
    
    class Meta:
        model = ExaminationType
        fields = ["name"]
