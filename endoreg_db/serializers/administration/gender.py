from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object
from endoreg_db.models.other.gender import Gender


class GenderSerializer(serializers.ModelSerializer[Gender]):
    """Serializer für Gender-Modell"""

    class Meta(_ModelSerializerMeta):
        model = Gender  # pyright: ignore[reportAssignmentType]
        fields = ["id", "name", "abbreviation", "description"]
        read_only_fields = ["id"]
