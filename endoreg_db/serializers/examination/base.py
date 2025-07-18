# endoreg_db/serializers.py
from rest_framework import serializers
from ...models import Examination

class ExaminationSerializer(serializers.ModelSerializer):
    from .optimized_examination_serializers import FindingSerializer
    findings = FindingSerializer(many=True, read_only=True, source='get_available_findings')

    class Meta:
        model = Examination
        fields = [
            'id', 'name', 'name_de', 'name_en', 'findings'
        ]
