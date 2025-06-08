from rest_framework import serializers
from endoreg_db.models import (
    Examination,
    Finding,
    FindingLocationClassification,
    FindingLocationClassificationChoice,
    FindingMorphologyClassification,
    FindingMorphologyClassificationChoice,
)

class FindingLocationClassificationChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FindingLocationClassificationChoice
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'subcategories', 'numerical_descriptors'
        ]

class FindingLocationClassificationSerializer(serializers.ModelSerializer):
    choices = FindingLocationClassificationChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = FindingLocationClassification
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'choices'
        ]

class FindingMorphologyClassificationChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FindingMorphologyClassificationChoice
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'subcategories', 'numerical_descriptors', 'classification'
        ]

class FindingMorphologyClassificationSerializer(serializers.ModelSerializer):
    choices = FindingMorphologyClassificationChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = FindingMorphologyClassification
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'classification_type', 'choices'
        ]

class FindingSerializer(serializers.ModelSerializer):
    optional_location_classifications = FindingLocationClassificationSerializer(many=True, read_only=True)
    required_location_classifications = FindingLocationClassificationSerializer(many=True, read_only=True)
    optional_morphology_classifications = FindingMorphologyClassificationSerializer(many=True, read_only=True)
    required_morphology_classifications = FindingMorphologyClassificationSerializer(many=True, read_only=True)

    class Meta:
        model = Finding
        fields = [
            'id', 'name', 'name_de', 'name_en', 'description', 'optional_location_classifications', 'required_location_classifications',
            'optional_morphology_classifications', 'required_morphology_classifications'
        ]

class ExaminationSerializer(serializers.ModelSerializer):
    findings = FindingSerializer(many=True, read_only=True, source='get_available_findings')

    class Meta:
        model = Examination
        fields = [
            'id', 'name', 'name_de', 'name_en', 'findings'
        ]
