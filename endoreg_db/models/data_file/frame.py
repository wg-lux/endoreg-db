from endoreg_db.models.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from .base_classes import AbstractFrame
from django.db import models

from .base_classes.utils import FRAME_DIR_NAME


class RawFrame(AbstractFrame):
    video = models.ForeignKey(
        "RawVideoFile", on_delete=models.CASCADE, related_name="frames"
    )


class Frame(AbstractFrame):
    video = models.ForeignKey("Video", on_delete=models.CASCADE, related_name="frames")
