from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers

if TYPE_CHECKING:
    _ModelSerializerMeta = serializers.ModelSerializer.Meta
else:
    _ModelSerializerMeta = object
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.ai.model_type import ModelType


class AiModelSerializer(serializers.ModelSerializer[AiModel]):
    """
    Serializer for the AiModel model.
    Handles 'model_type' relationship by its name.
    """

    model_type = serializers.SlugRelatedField(  # pyright: ignore[reportUnknownVariableType]
        queryset=ModelType.objects.all(),
        slug_field="name",
        allow_null=True,  # Match model field definition
        required=False,  # Match model field definition (blank=True)
    )

    class Meta(_ModelSerializerMeta):
        model = AiModel  # pyright: ignore[reportAssignmentType]
        fields = "__all__"
