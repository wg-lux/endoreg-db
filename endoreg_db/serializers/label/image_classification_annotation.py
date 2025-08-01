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
        """
        Retrieve the confidence score associated with the annotation.
        
        Returns:
            The float value representing the annotation's confidence, or None if not set.
        """
        if obj.float_value is not None:
            return obj.float_value
        return None

    def get_label_name(self, obj: ImageClassificationAnnotation) -> str:
        """
        Returns the name of the label associated with the annotation, or "No Label" if no label is set.
        """
        return obj.label.name if obj.label else "No Label"

    def to_representation(self, instance: ImageClassificationAnnotation) -> dict:
        """
        Return the serialized representation of an ImageClassificationAnnotation instance.
        
        Overrides the default method to allow for future customization of the serialized output, but currently returns the standard representation unchanged.
        
        Parameters:
            instance (ImageClassificationAnnotation): The annotation instance to serialize.
        
        Returns:
            dict: The serialized data for the annotation.
        """
        representation = super().to_representation(instance)
        # Add any additional fields or transformations here if needed
        return representation
