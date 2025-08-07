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
        """
        Retrieve and serialize all 'location' classifications associated with the given Finding instance.
        
        Returns:
            list: Serialized data for each related classification of type 'location'.
        """
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="location"
        )
        return FindingClassificationSerializer(classifications, many=True).data

    def get_morphology_classifications(self, obj):
        """
        Return serialized morphology classifications associated with the given finding.
        
        Parameters:
            obj: The Finding instance whose morphology classifications are retrieved.
        
        Returns:
            list: Serialized data for all related morphology classifications.
        """
        classifications = obj.finding_classifications.filter(
            classification_types__name__iexact="morphology"
        )
        return FindingClassificationSerializer(classifications, many=True).data

    def get_classifications(self, obj):
        """
        Retrieve all classifications related to the given finding.
        
        Returns:
            list: Serialized representations of all classifications associated with the finding.
        """
        return FindingClassificationSerializer(obj.finding_classifications.all(), many=True).data
