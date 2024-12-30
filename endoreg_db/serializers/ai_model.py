from rest_framework import serializers

from endoreg_db.models import ActiveModel, ModelMeta, ModelType


class ModelMetaSerializer(serializers.ModelSerializer):
    weights = serializers.FileField(
        use_url=True
    )  # use_url=True will provide the file's URI

    class Meta:
        model = ModelMeta
        fields = "__all__"


class ModelTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelType
        fields = "__all__"


class ActiveModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActiveModel
        fields = "__all__"
