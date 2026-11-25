from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from endoreg_db.models.utils import NameNaturalKeyManager

if TYPE_CHECKING:
    from .video_segmentation_label import VideoSegmentationLabel


class VideoSegmentationLabelSet(models.Model):
    name: models.CharField[Any, Any] = models.CharField(max_length=255)
    # German display name; NULL when the catalogue carries none.
    name_de: models.CharField[Any, Any] = models.CharField(
        max_length=255, blank=True, null=True
    )
    description: models.TextField[str | None, Any] = models.TextField(
        blank=True, null=True
    )
    labels: models.ManyToManyField[VideoSegmentationLabel, VideoSegmentationLabel] = (
        models.ManyToManyField("VideoSegmentationLabel", related_name="labelsets")
    )

    objects = NameNaturalKeyManager["VideoSegmentationLabelSet"]()

    if TYPE_CHECKING:
        pass

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
