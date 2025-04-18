from rest_framework import serializers
from endoreg_db.models import Examination
from .examination_types import ExaminationTypesSerializer

class LabelSerializer(serializers.ModelSerializer):
    """Serializer for Examination representation."""
    examination_types = ExaminationTypesSerializer(many=True, read_only=True)

    class Meta:
        model = Examination
        fields = ["name", "examination_types"]
  