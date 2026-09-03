from __future__ import annotations

from typing import TypeAlias, Any

from django.db import models

NoVideoSegmentationLabelValue: TypeAlias = None
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

    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    description: models.TextField[Any, Any] = models.TextField(blank=True, null=True)
    color: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    order_priority: models.IntegerField[Any, Any] = models.IntegerField(default=0)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
