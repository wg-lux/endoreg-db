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
        name_de (str): The German name of the label.
        name_en (str): The English name of the label.
        description (str): A description of the label.
        color (str): The color associated with the label.
        order_priority (int): The priority for ordering labels.
    """
    objects = VideoSegmentationLabelManager()

    name = models.CharField(max_length=255)
    name_de = models.CharField(max_length=255, blank=True, null=True)
    name_en = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    color = models.CharField(max_length=255, blank=True, null=True)
    order_priority = models.IntegerField(default=0)

    def natural_key(self):
        return (self.name,)

    def __str__(self):
        return self.name

class VideoSegmentationAnnotation(models.Model):
    """
    Represents a video segmentation annotation.

    Attributes:
        video (ForeignKey): The associated video file.
        label (ForeignKey): The label for the annotation.
        start_time (float): The start time of the annotation in seconds.
        stop_time (float): The stop time of the annotation in seconds.
        is_true (bool): Indicates if the annotation is valid.
    """
    video = models.ForeignKey("RawVideoFile", on_delete=models.CASCADE)
    label = models.ForeignKey(VideoSegmentationLabel, on_delete=models.CASCADE)
    start_time = models.FloatField()  # in seconds
    stop_time = models.FloatField()  # in seconds
    is_true = models.BooleanField(default=True)