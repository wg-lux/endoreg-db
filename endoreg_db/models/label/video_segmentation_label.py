from __future__ import annotations

from types import NoneType
from typing import TypeAlias

from django.db import models

NoVideoSegmentationLabelValue: TypeAlias = NoneType
VideoSegmentationLabelText: TypeAlias = "str | NoVideoSegmentationLabelValue"


class VideoSegmentationLabelManager(models.Manager["VideoSegmentationLabel"]):
    """
    Manager for VideoSegmentationLabel with custom query methods.
    """

    def get_by_natural_key(self, name: str) -> "VideoSegmentationLabel":
        return self.get(name=name)


class VideoSegmentationLabel(models.Model):
    """
    Represents a label for video segmentation annotations.

    Attributes:
        name (str): The name of the label.
        description (str): A description of the label.
        color (str): The color associated with the label.
        order_priority (int): The priority for ordering labels.
    """

    objects = VideoSegmentationLabelManager()

    name: models.CharField[str, str] = models.CharField(max_length=255)
    description: models.TextField[
        VideoSegmentationLabelText, VideoSegmentationLabelText
    ] = models.TextField(blank=True, null=True)
    color: models.CharField[
        VideoSegmentationLabelText, VideoSegmentationLabelText
    ] = models.CharField(max_length=255, blank=True, null=True)
    order_priority: models.IntegerField[int, int] = models.IntegerField(default=0)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
