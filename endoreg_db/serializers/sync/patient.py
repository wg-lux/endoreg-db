from rest_framework import serializers
from endoreg_db.models import Patient

from .center import CenterSerializer

class PatientSerializer(serializers.ModelSerializer):
    """Serializer for Patient representation."""
    center = CenterSerializer()
    class Meta:
        model = Patient
        fields = ['first_name', 'last_name', "center", "gender", "dob"]
