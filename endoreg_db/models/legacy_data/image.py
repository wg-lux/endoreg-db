from django.db import models
from ..label.label import Label
from ..annotation.image_classification import ImageClassificationAnnotation

class LegacyImage(models.Model):
    # Add any other fields you need to store frame-related information
    image = models.ImageField(upload_to="legacy_images")  # Or some other field type, depending on how you're storing the frame
    suffix = models.CharField(max_length=255)
    # ImageClassificationAnnotation has a foreign key to this model (related name: image_classification_annotations)

    def get_classification_annotations(self):
        """
        Get all image classification annotations for this image.
        """
        return ImageClassificationAnnotation.objects.filter(legacy_image=self)
    
    def get_classification_annotations_by_label(self, label:Label):
        """
        Get all image classification annotations for this image with the given label.
        """
        return ImageClassificationAnnotation.objects.filter(legacy_image=self, label=label)
    
    def get_classification_annotations_by_value(self, value:bool):
        """
        Get all image classification annotations for this image with the given value.
        """
        return ImageClassificationAnnotation.objects.filter(legacy_image=self, value=value)
    
    def get_classification_annotations_by_label_and_value(self, label:Label, value:bool):
        """
        Get all image classification annotations for this image with the given label and value.
        """
        return ImageClassificationAnnotation.objects.filter(legacy_image=self, label=label, value=value)
    
