from typing import TYPE_CHECKING

from django.db import models
from django.db.models import CheckConstraint, F, Q

if TYPE_CHECKING:
    from ...media.video.video_file import VideoFile
    from ..video_segmentation_label import VideoSegmentationLabel


class VideoSegmentationAnnotation(models.Model):
    """
    Represents a video segmentation annotation, linking a label to a time segment
    within a specific video file.

    An annotation must be associated with exactly one `VideoFile`.

    Attributes:
        video_file (ForeignKey): The video file associated with the annotation.
        label (ForeignKey): The label for the annotation.
        start_time (float): The start time of the annotation in seconds.
        stop_time (float): The stop time of the annotation in seconds.
        is_true (bool): Indicates if the annotation is valid (defaults to True).
    """

    # Foreign key to the unified VideoFile model.
    video_file = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="video_segmentation_annotations",
        null=False,
        blank=False,
    )

    label = models.ForeignKey("VideoSegmentationLabel", on_delete=models.CASCADE)
    start_time = models.FloatField()  # in seconds
    stop_time = models.FloatField()  # in seconds
    is_true = models.BooleanField(default=True)

    if TYPE_CHECKING:
        video_file: models.ForeignKey["VideoFile"]
        label: models.ForeignKey["VideoSegmentationLabel"]

    def __str__(self) -> str:
        """
        String representation of the annotation.
        """
        video_repr = self.get_video()  # Get the actual video object for representation
        return f"{video_repr} - {self.label.name} - {self.start_time} to {self.stop_time}"

    def get_video(self) -> "VideoFile":
        """
        Get the video file associated with this annotation.

        Returns the `VideoFile` instance.
        """
        if self.video_file:
            return self.video_file
        else:
            # This state should ideally not be reachable due to null=False, blank=False
            raise ValueError("Annotation is not linked to a VideoFile.")

    class Meta:
        constraints = [
            CheckConstraint(
                check=Q(start_time__lt=F("stop_time")),
                name="start_time_less_than_stop_time",
            ),
        ]
