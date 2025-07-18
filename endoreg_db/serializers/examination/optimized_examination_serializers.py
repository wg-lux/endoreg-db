from rest_framework import serializers
from endoreg_db.models import (
    Examination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
)

# class FindingClassificationChoiceSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = FindingClassificationChoice
#         fields = [
#             'id', 'name', 'name_de', 'name_en', 'description', 'subcategories', 'numerical_descriptors'
#         ]

# class FindingClassificationSerializer(serializers.ModelSerializer):
#     choices = FindingClassificationChoiceSerializer(many=True, read_only=True)

#     class Meta:
#         model = FindingClassification
#         fields = [
#             'id', 'name', 'name_de', 'name_en', 'description', 'choices', 'classification_types'
#         ]



# Moved to ./base.py
# class ExaminationSerializer(serializers.ModelSerializer):
#     findings = FindingSerializer(many=True, read_only=True, source='get_available_findings')

#     class Meta:
#         model = Examination
#         fields = [
#             'id', 'name', 'name_de', 'name_en', 'findings'
#         ]
