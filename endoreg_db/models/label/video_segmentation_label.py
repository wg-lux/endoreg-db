from django.db import models


class VideoSegmentationLabelManager(models.Manager):
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

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=255, blank=True, null=True)
    order_priority = models.IntegerField(default=0)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def __str__(self) -> str:
        return str(self.name)
