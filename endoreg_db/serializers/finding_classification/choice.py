from endoreg_db.models import FindingClassificationChoice
from rest_framework import serializers

class FindingClassificationChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FindingClassificationChoice
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'subcategories', 'numerical_descriptors'
        ]
