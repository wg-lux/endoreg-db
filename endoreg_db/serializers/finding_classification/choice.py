from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassificationChoice,
)
from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class FindingClassificationChoiceSerializer(
    serializers.ModelSerializer[FindingClassificationChoice]
):
    """
    Serializer for the FindingClassificationChoice model.

    Serializes the following fields:
        - id: Unique identifier of the classification choice.
        - name: Name of the classification choice.
        - description: Description of the classification choice.
        - subcategories: Related subcategories for further classification.
        - numerical_descriptors: Associated numerical descriptors for the classification choice.
    """

    class Meta(_ModelSerializerMeta):
        model = FindingClassificationChoice  # pyright: ignore[reportAssignmentType]
        fields = ["id", "name", "description", "subcategories", "numerical_descriptors"]
