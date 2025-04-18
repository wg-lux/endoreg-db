from rest_framework import serializers
from endoreg_db.models import ExaminationType

class ExportExaminationTypesSerializer(serializers.ModelSerializer):
    """Serializer for ExaminationType representation."""
    
    class Meta:
        model = ExaminationType
        fields = ["name", "name_de", "name_en", "examination_types"]
