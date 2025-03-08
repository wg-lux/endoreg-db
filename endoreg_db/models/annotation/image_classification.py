from django.db import models

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from endoreg_db.models import Frame, RawFrame, Label


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

    raw_frame = models.ForeignKey(
        "RawFrame",
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
    float_value = models.FloatField(blank=True, null=True)
    annotator = models.CharField(max_length=255, blank=True, null=True)
    model_meta = models.ForeignKey(
        "ModelMeta",
        on_delete=models.SET_NULL,
        related_name="image_classification_annotations",
        default=None,
        null=True,
        blank=True,
    )
    date_created = models.DateTimeField(auto_now_add=True)
    date_modified = models.DateTimeField(auto_now=True)
    information_source = models.ForeignKey(
        "InformationSource",
        on_delete=models.SET_NULL,
        related_name="image_classification_annotations",
        default=None,
        null=True,
        blank=True,
    )

    if TYPE_CHECKING:
        frame: "Frame"
        raw_frame: "RawFrame"
        label: "Label"

    def __str__(self) -> str:
        """
        String representation of the annotation.
        """
        return f"{self.label.name} - {self.value}"

    def get_frame(self):
        """
        Get the frame associated with the annotation.
        """
        if self.frame:
            return self.frame
        else:
            return self.raw_frame
