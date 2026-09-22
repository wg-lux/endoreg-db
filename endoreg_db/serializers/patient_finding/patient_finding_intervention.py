# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from typing import cast, TYPE_CHECKING

from endoreg_db.serializers.misc.translatable_field_mix_in import TranslatableFieldMixin
from endoreg_db.serializers.misc.translatable_field_mix_in import _TranslatableFieldLike
from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class PatientFindingInterventionSerializer(
    serializers.ModelSerializer[PatientFindingIntervention], TranslatableFieldMixin
):
    """Optimierter Serializer für PatientFindingIntervention"""

    intervention_name = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = PatientFindingIntervention  # pyright: ignore[reportAssignmentType]
        fields = ["id", "intervention", "intervention_name", "state"]

    def get_intervention_name(self, obj: PatientFindingIntervention) -> str:
        """
        Return the localized name of the intervention associated with the given object.

        Parameters:
            obj: The object containing the intervention to be localized.

        Returns:
            str: The localized name of the intervention.
        """
        return self.get_localized_name(cast(_TranslatableFieldLike, obj.intervention))
