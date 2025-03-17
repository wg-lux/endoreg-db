from typing import TYPE_CHECKING

from endoreg_db.models.label.label import Label
from .base_classes import AbstractFrame
from django.db import models

from .base_classes.utils import FRAME_DIR_NAME, RAW_FRAME_DIR_NAME

if TYPE_CHECKING:
    from endoreg_db.models import RawVideoFile, Video


class RawFrame(AbstractFrame):
    image = models.ImageField(upload_to=RAW_FRAME_DIR_NAME, blank=True, null=True)
    video = models.ForeignKey(
        "RawVideoFile", on_delete=models.CASCADE, related_name="frames"
    )

    if TYPE_CHECKING:
        video: "RawVideoFile"
        label: "Label"


class Frame(AbstractFrame):
    image = models.ImageField(upload_to=FRAME_DIR_NAME, blank=True, null=True)
    video = models.ForeignKey("Video", on_delete=models.CASCADE, related_name="frames")

    if TYPE_CHECKING:
        video: "Video"
