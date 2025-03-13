from typing import Union, TYPE_CHECKING
from endoreg_db.models.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label

from django.db import models
from .utils import FRAME_DIR_NAME

if TYPE_CHECKING:
    from endoreg_db.models import Video, RawVideoFile


class AbstractFrame(models.Model):
    frame_number = models.IntegerField()
    # Add any other fields you need to store frame-related information
    suffix = models.CharField(max_length=255)
    # ImageClassificationAnnotation has a foreign key to this model (related name: image_classification_annotations)
    # image = models.ImageField(blank=True, null=True)
    is_raw = models.BooleanField(default=False)
    extracted = models.BooleanField(default=False)

    if TYPE_CHECKING:
        video: Union["Video", "RawVideoFile"]
        image_classification_annotations: (
            "models.QuerySet[ImageClassificationAnnotation]"
        )

    class Meta:
        # Ensure that for each video, the frame_number is unique
        abstract = True
        unique_together = ("video", "frame_number")
        # Optimize for retrieval in frame_number order
        indexes = [models.Index(fields=["video", "frame_number"])]

    # Override save method to set is_raw based on the type of frame
    def save(self, *args, **kwargs):
        self.is_raw = self.is_raw_frame()
        super().save(*args, **kwargs)

    def get_classification_annotations(self):
        """
        Get all image classification annotations for this frame.
        """
        if self.is_raw_frame():
            return ImageClassificationAnnotation.objects.filter(raw_frame=self)
        return ImageClassificationAnnotation.objects.filter(frame=self)

    def get_classification_annotations_by_label(self, label: Label):
        """
        Get all image classification annotations for this frame with the given label.
        """
        if self.is_raw_frame():
            return ImageClassificationAnnotation.objects.filter(
                raw_frame=self, label=label
            )
        return ImageClassificationAnnotation.objects.filter(frame=self, label=label)

    def get_classification_annotations_by_value(self, value: bool):
        """
        Get all image classification annotations for this frame with the given value.
        """
        if self.is_raw_frame():
            return ImageClassificationAnnotation.objects.filter(
                raw_frame=self, value=value
            )
        return ImageClassificationAnnotation.objects.filter(frame=self, value=value)

    def get_classification_annotations_by_label_and_value(
        self, label: Label, value: bool
    ):
        """
        Get all image classification annotations for this frame with the given label and value.
        """
        if self.is_raw_frame():
            return ImageClassificationAnnotation.objects.filter(
                raw_frame=self, label=label, value=value
            )
        return ImageClassificationAnnotation.objects.filter(
            frame=self, label=label, value=value
        )

    def __str__(self):
        return self.video.file.path + " - " + str(self.frame_number)

    def is_raw_frame(self):
        from endoreg_db.models import RawFrame

        if isinstance(self, RawFrame):
            return True
        else:
            return False

    def get_frame_model(self):
        from endoreg_db.models import Frame, RawFrame

        if self.is_raw_frame():
            return RawFrame
        else:
            return Frame
