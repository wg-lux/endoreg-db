from endoreg_db.models import FindingClassificationChoice
from rest_framework import serializers

class FindingClassificationChoiceSerializer(serializers.ModelSerializer):
    """
    Serializer for the FindingClassificationChoice model.

    Serializes the following fields:
        - id: Unique identifier of the classification choice.
        - name: Name of the classification choice.
        - description: Description of the classification choice.
        - subcategories: Related subcategories for further classification.
        - numerical_descriptors: Associated numerical descriptors for the classification choice.
    """
    class Meta:
        model = FindingClassificationChoice
        fields = [
            'id', 'name', 'description', 'subcategories', 'numerical_descriptors'
        ]
