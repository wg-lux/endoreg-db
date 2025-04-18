from rest_framework import serializers
from endoreg_db.models import (
    VideoPredictionMeta,
    ModelMeta,
    AiModel,
    LabelSet,
    Label,
    LabelType,
    VideoSegmentationLabelSet
)

class LabelTypeSerializer(serializers.ModelSerializer):
    """Serializer for LabelType representation."""
    class Meta:
        model = LabelType
        fields = [
            "name",
            "description"
        ]
class LabelSerializer(serializers.ModelSerializer):
    """Serializer for Label representation."""

    class Meta:
        model = Label
        fields = [
            "name",
            "label_type",
            "description",
        ]

class LabelSetSerializer(serializers.ModelSerializer):
    """Serializer for LabelSet representation."""

    labels = LabelSerializer(many=True, read_only=True)

    class Meta:
        model = LabelSet
        fields = [
            "name", "description",
            "version", "labels"
    ]
     
class VideoSegmentationLabelSetSerializer(serializers.ModelSerializer):
    """Serializer for VideoSegmentationLabelSet representation."""
    labels = LabelSerializer(many=True, read_only=True)

    class Meta:
        model = VideoSegmentationLabelSet
        fields = [
            "name", "description", "labels"
        ]


class AiModelSerializer(serializers.ModelSerializer):
    """Serializer for AiModel representation."""
    labelset = LabelSetSerializer(many=False, read_only=True)
    class Meta:
        model = AiModel
        fields = [
            "name",
            "version",
            "labelset",
            "activation",
            "mean",
            "std",
            "size_x",
            "size_y",
            "axes", 
            "batchsize",
            "num_workers",
        ]

class ModelMetaSerializer(serializers.ModelSerializer):
    """Serializer for ModelMeta representation."""
    labelset = LabelSetSerializer(many=False, read_only=True)
    weights = serializers.SerializerMethodField()
    model = AiModelSerializer(many=False, read_only=True)

    def get_weights(self, obj:ModelMeta):
        """Get the file path of the weights."""
        if obj.weights:
            return obj.weights.file.path
        return None
    class Meta:
        model = ModelMeta
        fields = [
            "name",
            "version",
            "model",
            "labelset",
            "activation",
            "weights", # get data file path
            "mean",
            "std",
            "size_x",
            "size_y",
            "axes", 
            "batchsize",
            "num_workers",
            "date_created",
        ]

class VideoPredictionMetaSerializer(serializers.ModelSerializer):
    """Serializer for VideoPredictionMeta representation."""
    video = serializers.CharField(source="video.uuid", read_only=True)
    model_meta = ModelMetaSerializer(many=False, read_only=True)
    class Meta:
        model = VideoPredictionMeta
        fields = [
            "model_meta",
            "date_created",
            "date_modified",
            "video",
            "frames",
            "label_video_segments",
            "prediction_type_name",
            "prediction_type_name_de",
            "prediction_type_name_en",
        ]
