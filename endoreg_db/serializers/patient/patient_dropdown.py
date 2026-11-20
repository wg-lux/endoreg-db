from __future__ import annotations

from typing import Protocol

from rest_framework import serializers

from endoreg_db.models.administration.person.patient.patient import Patient


class _PatientDropdownLike(Protocol):
    pk: int | None
    first_name: str
    last_name: str


class PatientDropdownSerializer(serializers.ModelSerializer[Patient]):
    """Serializer für Patient-Dropdown"""

    display_name = serializers.SerializerMethodField()

    class Meta:  # pyright: ignore[reportIncompatibleVariableOverride]
        model = Patient
        fields = [
            "id",
            "first_name",
            "last_name",
            "display_name",
            "dob",
        ]

    def get_display_name(self, obj: _PatientDropdownLike) -> str:
        """Identify the local record without exposing its matching hash."""
        return f"{obj.first_name} {obj.last_name} (ID {obj.pk})"
