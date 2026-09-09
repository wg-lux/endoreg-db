# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from typing import cast, TYPE_CHECKING

from endoreg_db.models.medical.patient.patient_finding_classification import (
    PatientFindingClassification,
)
from endoreg_db.serializers.misc.translatable_field_mix_in import (
    TranslatableFieldMixin,
    _TranslatableFieldLike,
)

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class PatientFindingClassificationSerializer(
    serializers.ModelSerializer[PatientFindingClassification], TranslatableFieldMixin
):
    """Serializer für PatientFinding-Klassifikationen"""

    classification_name = serializers.SerializerMethodField()
    classification_choice_name = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = PatientFindingClassification  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "classification",
            "classification_name",
            "classification_choice_name",
        ]

    def get_classification_name(self, obj: PatientFindingClassification):
        """
        Return the localized name for the classification attribute of a PatientFindingClassification instance.

        Parameters:
            obj (PatientFindingClassification): The instance whose classification name is to be localized.

        Returns:
            str: The localized classification name.
        """
        return self.get_localized_name(cast(_TranslatableFieldLike, obj.classification))

    def get_classification_choice_name(self, obj: PatientFindingClassification):
        """
        Return the localized name for the classification choice of a patient finding classification instance.

        Parameters:
            obj (PatientFindingClassification): The patient finding classification instance.

        Returns:
            str: Localized name of the classification choice.
        """
        return self.get_localized_name(
            cast(_TranslatableFieldLike, obj.classification_choice)
        )
