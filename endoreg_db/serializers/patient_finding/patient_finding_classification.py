# pyright: reportUnusedFunction=false, reportMissingTypeStubs=false
from typing import TYPE_CHECKING

from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


def _name(value: object) -> str:
    return str(getattr(value, "name", "") or "")


def _name_de(value: object) -> str | None:
    """Catalogue German name; None when absent, never the identifier."""
    name_de = getattr(value, "name_de", None)
    return name_de if isinstance(name_de, str) and name_de.strip() else None


class PatientFindingClassificationSerializer(
    serializers.ModelSerializer[PatientFindingClassification]
):
    """Serializer für PatientFinding-Klassifikationen.

    ``*_name`` fields are identifiers; ``*_name_de`` are German display names.
    """

    classification_name = serializers.SerializerMethodField()
    classification_name_de = serializers.SerializerMethodField()
    classification_choice_name = serializers.SerializerMethodField()
    classification_choice_name_de = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = PatientFindingClassification  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "classification",
            "classification_name",
            "classification_name_de",
            "classification_choice_name",
            "classification_choice_name_de",
        ]

    def get_classification_name(self, obj: PatientFindingClassification) -> str:
        return _name(obj.classification)

    def get_classification_name_de(
        self, obj: PatientFindingClassification
    ) -> str | None:
        return _name_de(obj.classification)

    def get_classification_choice_name(self, obj: PatientFindingClassification) -> str:
        return _name(obj.classification_choice)

    def get_classification_choice_name_de(
        self, obj: PatientFindingClassification
    ) -> str | None:
        return _name_de(obj.classification_choice)
