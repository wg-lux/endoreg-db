from rest_framework import serializers
from endoreg_db.models import Examination
from .examination_types import ExportExaminationTypesSerializer

class ExportExaminationSerializer(serializers.ModelSerializer):
    """Serializer for Examination representation."""
    examination_types = ExportExaminationTypesSerializer(many=True, read_only=True)

    class Meta:
        model = Examination
        fields = ["name", "name_de", "name_en", "examination_types"]
  