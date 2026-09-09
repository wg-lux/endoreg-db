from __future__ import annotations

from typing import Protocol

from rest_framework import serializers

from endoreg_db.models.administration.person.patient.patient import Patient


class _PatientDropdownLike(Protocol):
    patient_hash: str | None
    first_name: str
    last_name: str


class PatientDropdownSerializer(serializers.ModelSerializer[Patient]):
    """Serializer für Patient-Dropdown"""

    display_name = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = Patient
        fields = [
            "id",
            "patient_hash",
            "first_name",
            "last_name",
            "display_name",
            "dob",
        ]

    def get_display_name(self, obj: _PatientDropdownLike) -> str:
        """
        Returns a user-friendly display string for a patient, combining their first and last name with a shortened patient hash or a placeholder if the hash is missing.

        Parameters:
            obj: The patient instance being serialized.

        Returns:
            str: The formatted display name for the patient.
        """
        patient_hash = obj.patient_hash
        hash_display = f"({patient_hash[:8]}...)" if patient_hash else "(No Hash)"
        return f"{obj.first_name} {obj.last_name} {hash_display}"
