from rest_framework import serializers
from agl_base_db.models import (
    Patient,
)

class PatientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = '__all__'

