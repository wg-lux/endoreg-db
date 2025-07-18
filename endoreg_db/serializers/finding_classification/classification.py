from .choice import FindingClassificationChoiceSerializer
from endoreg_db.models import FindingClassification
from rest_framework import serializers

class FindingClassificationSerializer(serializers.ModelSerializer):
    choices = FindingClassificationChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = FindingClassification
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'choices', 'classification_types'
        ]
    