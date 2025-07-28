from typing import Optional
from rest_framework import serializers


from endoreg_db.models import ImageClassificationAnnotation


class ImageClassificationAnnotationSerializer(serializers.ModelSerializer):
    """
    Serializer for ImageClassificationAnnotation model.
    This serializer is used to represent image classification annotations in the API.
    """

    label_name = serializers.SerializerMethodField()
    confidence = serializers.SerializerMethodField()
    frame_number = serializers.IntegerField(source='frame.frame_number', read_only=True)

    class Meta:
        model = ImageClassificationAnnotation
        fields = [
            "id",
            "label",
            "confidence",
            "frame_number",
            "information_source",
            "date_created",
            "date_modified",
            "label_name",
        ]

    def get_confidence(self, obj: ImageClassificationAnnotation) -> Optional[float]:
        if obj.float_value is not None:
            return obj.float_value
        return None

    def get_label_name(self, obj: ImageClassificationAnnotation) -> str:
        """
        Get the actual label name from the related Label model.
        This method is used to retrieve the label name for the annotation.
        """
        return obj.label.name if obj.label else "No Label"

    def to_representation(self, instance: ImageClassificationAnnotation) -> dict:
        """
        Custom representation to include additional fields if necessary.
        """
        representation = super().to_representation(instance)
        # Add any additional fields or transformations here if needed
        return representation
