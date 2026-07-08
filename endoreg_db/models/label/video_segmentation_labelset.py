from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, TypeAlias, Any

from django.db import models

if TYPE_CHECKING:
    from .video_segmentation_label import VideoSegmentationLabel

NoVideoSegmentationLabelSetValue: TypeAlias = NoneType
VideoSegmentationLabelSetDescription: TypeAlias = (
    "str | NoVideoSegmentationLabelSetValue"
)


class VideoSegmentationLabelSetManager(models.Manager["VideoSegmentationLabelSet"]):
    def get_by_natural_key(self, name: str) -> "VideoSegmentationLabelSet":
        return self.get(name=name)


class VideoSegmentationLabelSet(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    description: models.TextField[VideoSegmentationLabelSetDescription, Any] = (
        models.TextField(blank=True, null=True)
    )
    labels: models.ManyToManyField[VideoSegmentationLabel, VideoSegmentationLabel] = (
        models.ManyToManyField("VideoSegmentationLabel", related_name="labelsets")
    )

    objects = VideoSegmentationLabelSetManager()

    if TYPE_CHECKING:
        pass

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
