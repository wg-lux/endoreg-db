from django.db import models


class ImageClassificationAnnotation(models.Model):
    """
    Represents an image classification annotation.

    Attributes:
        frame (ForeignKey): The frame associated with the annotation.
        legacy_frame (ForeignKey): The legacy frame associated with the annotation.
        legacy_image (ForeignKey): The legacy image associated with the annotation.
        label (ForeignKey): The label assigned to the annotation.
        value (bool): Indicates if the classification is valid.
        annotator (str): The user who created the annotation.
        date_created (datetime): The creation date of the annotation.
        date_modified (datetime): The last modification date of the annotation.
    """

    # Foreign keys to Frame, LegacyFrame, and LegacyImage (only one of these should be set)
    frame = models.ForeignKey(
        "Frame",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="image_classification_annotations",
    )
    legacy_frame = models.ForeignKey(
        "LegacyFrame",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="image_classification_annotations",
    )
    legacy_image = models.ForeignKey(
        "LegacyImage",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="image_classification_annotations",
    )

    label = models.ForeignKey(
        "Label",
        on_delete=models.CASCADE,
        related_name="image_classification_annotations",
    )
    value = models.BooleanField()
    annotator = models.CharField(max_length=255)
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        """
        String representation of the annotation.
        """
        return f"{self.label.name} - {self.value}"
