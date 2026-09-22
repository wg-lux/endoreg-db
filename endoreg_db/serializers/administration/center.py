from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object
from endoreg_db.models.administration.center.center import Center


class CenterSerializer(serializers.ModelSerializer[Center]):
    """Serializer für Center-Modell"""

    center_key = serializers.CharField(read_only=True)
    name = serializers.CharField(source="display_name", read_only=True)

    class Meta(_ModelSerializerMeta):
        model = Center  # pyright: ignore[reportAssignmentType]
        fields = [
            "id",
            "center_key",
            "name",
            "display_name",
        ]
        read_only_fields = ["id", "center_key", "name", "display_name"]
