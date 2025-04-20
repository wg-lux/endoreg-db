from django.db import models

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models import VideoSegmentationLabel

class VideoSegmentationLabelSetManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class VideoSegmentationLabelSet(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    labels = models.ManyToManyField("VideoSegmentationLabel", related_name="labelsets")

    objects = VideoSegmentationLabelSetManager()

    if TYPE_CHECKING:
        labels: models.QuerySet["VideoSegmentationLabel"]

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return str(self.name)
