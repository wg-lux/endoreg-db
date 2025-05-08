from django.db import models
# ...existing code...

class VideoFile(models.Model):
    # ...existing code...

    # Reverse relationships
    video_prediction_meta = models.ForeignKey(
        'VideoPredictionMeta',
        related_name='video_files',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    video_segmentation_annotations = models.ForeignKey(
        'VideoSegmentationAnnotation',
        related_name='video_files',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    # ...existing code...
