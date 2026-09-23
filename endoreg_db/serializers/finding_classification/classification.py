from .choice import FindingClassificationChoiceSerializer
from endoreg_db.models.medical.finding.finding_classification import (
    FindingClassification,
)
from rest_framework import serializers


class FindingClassificationSerializer(
    serializers.ModelSerializer[FindingClassification]
):
    choices = FindingClassificationChoiceSerializer(many=True, read_only=True)

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = FindingClassification
        fields = ["id", "name", "description", "choices", "classification_types"]
