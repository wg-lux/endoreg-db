from django.db import models

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..label import Label
    from ...media.frame import Frame
    from ...metadata import ModelMeta
    from ...other.information_source import InformationSource

class ImageClassificationAnnotation(models.Model):
    """
    Represents an image classification annotation, linking a label to a specific frame.

    An annotation is associated with a single `Frame` instance, which itself
    links to either a `Video` or `RawVideoFile`.

    Attributes:
        frame (ForeignKey): The frame associated with the annotation.
        label (ForeignKey): The label assigned to the annotation.
        value (bool): Indicates if the classification is true/positive.
        float_value (FloatField): Optional float value associated with the classification (e.g., confidence score).
        annotator (str): Identifier for the user or process that created the annotation.
        model_meta (ForeignKey): Optional link to the model metadata if generated by a model.
        date_created (datetime): The creation timestamp of the annotation.
        date_modified (datetime): The last modification timestamp of the annotation.
        information_source (ForeignKey): Optional link to the source of this information.
    """

    # Single ForeignKey to the unified Frame model
    frame = models.ForeignKey(
        "Frame",  # Points to the unified Frame model
        on_delete=models.CASCADE,
        related_name="image_classification_annotations",
        blank=False,
        null=False,
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
        frame: "Frame"  # Updated type hint
        label: "Label"
        information_source: "InformationSource"
        model_meta: "ModelMeta"  # Added for completeness

    class Meta:
        indexes = [
            models.Index(fields=['frame', 'label']),
            models.Index(fields=['frame']),
        ]

    def __str__(self) -> str:
        """
        String representation of the annotation.
        """
        frame_str = str(self.frame) if self.frame else "No Frame"
        label_name = self.label.name if self.label else "No Label"
        return f"{frame_str} - {label_name} - {self.value}"
