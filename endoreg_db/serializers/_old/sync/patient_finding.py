from rest_framework import serializers
from endoreg_db.models import PatientFinding

class PatientFindingSerializer(serializers.ModelSerializer):
    """Serializer for PatientFinding representation."""
    class Meta:
        model = PatientFinding
        fields = []