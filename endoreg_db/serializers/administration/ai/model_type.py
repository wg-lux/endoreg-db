from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object
from endoreg_db.models.administration.ai.model_type import ModelType


class ModelTypeSerializer(serializers.ModelSerializer[ModelType]):
    """
    Serializer for the ModelType model.
    """

    class Meta(_ModelSerializerMeta):
        model = ModelType  # pyright: ignore[reportAssignmentType]
        fields = "__all__"
