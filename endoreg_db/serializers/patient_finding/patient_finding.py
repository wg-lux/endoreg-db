from endoreg_db.models import PatientFinding
from rest_framework import serializers

class PatientFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientFinding
        fields = '__all__'