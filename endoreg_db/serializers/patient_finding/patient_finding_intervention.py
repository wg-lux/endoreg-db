# pyright: reportUnusedFunction=false, reportMissingTypeStubs=false
from typing import TYPE_CHECKING

from endoreg_db.models.medical.patient.patient_finding_intervention import (
    PatientFindingIntervention,
)

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class PatientFindingInterventionSerializer(
    serializers.ModelSerializer[PatientFindingIntervention]
):
    """Optimierter Serializer für PatientFindingIntervention.

    ``intervention_name`` is the identifier; ``intervention_name_de`` the German
    display name, or null when the catalogue carries none.
    """

    intervention_name = serializers.SerializerMethodField()
    intervention_name_de = serializers.SerializerMethodField()

    class Meta(_ModelSerializerMeta):
        model = PatientFindingIntervention  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "intervention",
            "intervention_name",
            "intervention_name_de",
            "state",
        ]

    def get_intervention_name(self, obj: PatientFindingIntervention) -> str:
        return str(getattr(obj.intervention, "name", "") or "")

    def get_intervention_name_de(self, obj: PatientFindingIntervention) -> str | None:
        name_de = getattr(obj.intervention, "name_de", None)
        return name_de if isinstance(name_de, str) and name_de.strip() else None
