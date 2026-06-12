from __future__ import annotations

from rest_framework import serializers

from endoreg_db.models.medical.examination.examination import Examination


class _ExaminationDropdownLike:
    name: str
    name_de: str | None
    pk: int


class ExaminationDropdownSerializer(serializers.ModelSerializer[Examination]):
    """Serializer für Examination-Dropdown"""

    display_name = serializers.SerializerMethodField()

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        model = Examination
        fields = ["id", "name", "display_name"]

    def get_display_name(self, obj: _ExaminationDropdownLike) -> str:
        """
        Return a user-friendly (localized) display name for the examination.
        Prefers a German translation (`name_de`) when available; otherwise falls back to `name`."""
        return obj.name_de or obj.name
