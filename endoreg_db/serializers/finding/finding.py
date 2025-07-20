from rest_framework import serializers
from ..finding_classification import (
    FindingClassificationSerializer,
    # FindingClassificationChoiceSerializer,
)
from endoreg_db.models import Finding

class FindingSerializer(serializers.ModelSerializer):
    
    location_classifications = serializers.SerializerMethodField()
    morphology_classifications = serializers.SerializerMethodField()
    classifications = serializers.SerializerMethodField()

    class Meta:
        model = Finding
        fields = [
            'id', 'name', 'description', "classifications", 'location_classifications', 'morphology_classifications',
        ]

    def get_location_classifications(self, obj):
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="location"
        )
        return FindingClassificationSerializer(classifications, many=True).data

    def get_morphology_classifications(self, obj):
        """
        Retrieves morphology classifications associated with the finding.
        Returns:
            list: A list of serialized morphology classifications related to the finding.
        """
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="morphology"
        )
        return FindingClassificationSerializer(classifications, many=True).data

    def get_classifications(self, obj):
        """
        Retrieves all classifications associated with the finding.
        Returns:
            list: A list of serialized classifications related to the finding.
        """
        return FindingClassificationSerializer(obj.finding_classifications.all(), many=True).data
