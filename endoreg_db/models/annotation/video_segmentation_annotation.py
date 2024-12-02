from django.db import models

class VideoSegmentationLabelManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)
    
class VideoSegmentationLabel(models.Model):
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
    video = models.ForeignKey("RawVideoFile", on_delete=models.CASCADE)
    label = models.ForeignKey(VideoSegmentationLabel, on_delete=models.CASCADE)
    start_time = models.FloatField()  # in seconds
    stop_time = models.FloatField()  # in seconds
    is_true = models.BooleanField(default=True)
