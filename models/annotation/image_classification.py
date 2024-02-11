from django.db import models

class ImageClassificationAnnotation(models.Model):
    """
    A class representing an image classification annotation.

    Attributes:
        label (str): The label that was assigned to the image.
        confidence (float): The confidence that the label is correct.
        annotator (User): The user that created this annotation.
        date (datetime.datetime): The date and time when this annotation was created.

    """
    # Foreign keys to Frame, LegacyFrame, and LegacyImage (only one of these should be set)
    frame = models.ForeignKey("Frame", on_delete=models.CASCADE, blank=True, null=True, related_name="image_classification_annotations")
    legacy_frame = models.ForeignKey("LegacyFrame", on_delete=models.CASCADE, blank=True, null=True, related_name="image_classification_annotations")
    legacy_image = models.ForeignKey("LegacyImage", on_delete=models.CASCADE, blank=True, null=True, related_name="image_classification_annotations")

    label = models.ForeignKey("Label", on_delete=models.CASCADE, related_name="image_classification_annotations")
    value = models.BooleanField()
    annotator = models.CharField(max_length=255)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.label.name + " - " + str(self.value)
    