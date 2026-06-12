from endoreg_db.models.label.label import Label


from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object


class LabelSerializer(serializers.ModelSerializer[Label]):
    """
    Serializer for fetching labels from the `endoreg_db_label` table.
    Includes `id` (for backend processing) and `name` (for dropdown display in Vue.js).
    """

    class Meta(_ModelSerializerMeta):
        model = Label  # pyright: ignore[reportAssignmentType]
        fields = ["id", "name"]
