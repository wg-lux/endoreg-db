from rest_framework import serializers
from ..finding_classification import (
    FindingClassificationSerializer,
    # FindingClassificationChoiceSerializer,
)
from endoreg_db.models import Finding

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