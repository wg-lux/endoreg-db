from typing import ClassVar, TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object
from endoreg_db.models.administration.ai.active_model import ActiveModel


class ActiveModelSerializer(serializers.ModelSerializer[ActiveModel]):
    """
    Serializer for the ActiveModel model.
    """

    class Meta(_ModelSerializerMeta):
        model: ClassVar[type[ActiveModel]] = ActiveModel  # pyright: ignore[reportIncompatibleVariableOverride]
        fields = "__all__"
