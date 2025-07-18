from rest_framework import serializers
from endoreg_db.models import (
    Examination,
    Finding,
    FindingClassification,
    FindingClassificationChoice,
)

class FindingClassificationChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FindingClassificationChoice
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'subcategories', 'numerical_descriptors'
        ]

class FindingClassificationSerializer(serializers.ModelSerializer):
    choices = FindingClassificationChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = FindingClassification
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'choices', 'classification_types'
        ]

class FindingSerializer(serializers.ModelSerializer):
    location_classifications = serializers.SerializerMethodField()
    morphology_classifications = serializers.SerializerMethodField()

    def get_location_classifications(self, obj):
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="location"
        )
        return FindingClassificationSerializer(classifications, many=True).data

    def get_morphology_classifications(self, obj):
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="morphology"
        )
        return FindingClassificationSerializer(classifications, many=True).data

    class Meta:
        model = Finding
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description',
            'classifications',
        ]

class ExaminationSerializer(serializers.ModelSerializer):
    findings = FindingSerializer(many=True, read_only=True, source='get_available_findings')

    class Meta:
        model = Examination
        fields = [
            'id', 'name', 'name_de', 'name_en', 'findings'
        ]
