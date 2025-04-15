from typing import TYPE_CHECKING

from endoreg_db.models.label.label import Label
from .base_classes import AbstractFrame
from django.db import models

from ...utils import data_paths

if TYPE_CHECKING:
    from endoreg_db.models import RawVideoFile, Video


class RawFrame(AbstractFrame):
    image = models.ImageField(upload_to=data_paths["raw_frame"], blank=True, null=True)
    video = models.ForeignKey(
        "RawVideoFile", on_delete=models.CASCADE, related_name="frames"
    )

    if TYPE_CHECKING:
        video: "RawVideoFile"
        label: "Label"


class Frame(AbstractFrame):
    image = models.ImageField(upload_to=data_paths["frame"], blank=True, null=True)
    video = models.ForeignKey("Video", on_delete=models.CASCADE, related_name="frames")

    if TYPE_CHECKING:
        video: "Video"
