from rest_framework import serializers
from endoreg_db.models.administration.ai.ai_model import AiModel
from endoreg_db.models.administration.ai.model_type import ModelType


class AiModelSerializer(serializers.ModelSerializer):
    """
    Serializer for the AiModel model.
    Handles 'model_type' relationship by its name.
    """

    model_type = serializers.SlugRelatedField(
        queryset=ModelType.objects.all(),
        slug_field="name",
        allow_null=True,  # Match model field definition
        required=False,  # Match model field definition (blank=True)
    )

    class Meta:
        model = AiModel
        fields = "__all__"
