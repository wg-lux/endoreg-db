from rest_framework import serializers
from endoreg_db.models.administration.ai.active_model import ActiveModel


class ActiveModelSerializer(serializers.ModelSerializer):
    """
    Serializer for the ActiveModel model.
    """

    class Meta:
        model = ActiveModel
        fields = "__all__"
