from __future__ import annotations

from rest_framework import serializers

from endoreg_db.models.medical.examination.examination import Examination


class ExaminationDropdownSerializer(serializers.ModelSerializer[Examination]):
    """Serializer für Examination-Dropdown"""

    display_name = serializers.SerializerMethodField()

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        model = Examination
        fields = ["id", "name", "display_name"]

    def get_display_name(self, obj: Examination) -> str:
        """Return the canonical examination name used by the current model."""
        return str(obj.name)
