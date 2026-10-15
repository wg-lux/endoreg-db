from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models
from django.db.models import CheckConstraint, F, Q


if TYPE_CHECKING:
    from ...media.video.video_file import VideoFile


class VideoSegmentationAnnotation(models.Model):
    """
    Represents a video segmentation annotation, linking a label to a time segment
    within a specific video file.

    An annotation must be associated with exactly one `VideoFile`.
    """

    video_file: models.ForeignKey[Any] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="video_segmentation_annotations",
    )

    label: models.ForeignKey[Any] = models.ForeignKey(
        "VideoSegmentationLabel",
        on_delete=models.CASCADE,
    )

    # times in seconds
    start_time: models.FloatField[Any, Any] = models.FloatField()
    stop_time: models.FloatField[Any, Any] = models.FloatField()

    is_true: models.BooleanField[Any, Any] = models.BooleanField(default=True)

    if TYPE_CHECKING:
        pass

    def __str__(self) -> str:
        return f"{self.video_file.pk} - {self.label.name} - {self.start_time} to {self.stop_time}"

    def get_video(self) -> "VideoFile":
        """
        Convenience accessor for the associated VideoFile instance.
        """
        return self.video_file

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(start_time__lt=F("stop_time")),
                name="start_time_less_than_stop_time",
            ),
        ]
        # optional but usually helpful:
        # ordering = ("video_file_id", "start_time")
