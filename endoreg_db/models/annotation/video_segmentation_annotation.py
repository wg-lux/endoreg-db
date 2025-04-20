from typing import TYPE_CHECKING, Union
from django.db import models
from django.db.models import Q, CheckConstraint, F

if TYPE_CHECKING:
    from endoreg_db.models.video.video import Video
    from endoreg_db.models.video.raw_video import RawVideoFile
    from endoreg_db.models.label.video_segmentation_label import VideoSegmentationLabel

class VideoSegmentationAnnotation(models.Model):
    """
    Represents a video segmentation annotation, linking a label to a time segment
    within a specific video or raw video file.

    An annotation must be associated with exactly one of either a `Video` or a `RawVideoFile`.
    This exclusivity is enforced by a database constraint.

    Attributes:
        video (ForeignKey): The processed video associated with the annotation (nullable).
        raw_video (ForeignKey): The raw video file associated with the annotation (nullable).
        label (ForeignKey): The label for the annotation.
        start_time (float): The start time of the annotation in seconds.
        stop_time (float): The stop time of the annotation in seconds.
        is_true (bool): Indicates if the annotation is valid (defaults to True).
    """
    # Foreign keys to Video and RawVideoFile. Exactly one must be non-null.
    video = models.ForeignKey(
        "Video",
        on_delete=models.CASCADE,
        related_name="video_segmentation_annotations",
        null=True,
        blank=True,
    )
    raw_video = models.ForeignKey(
        "RawVideoFile",
        on_delete=models.CASCADE,
        related_name="video_segmentation_annotations",
        null=True,
        blank=True,
    )

    label = models.ForeignKey("VideoSegmentationLabel", on_delete=models.CASCADE)
    start_time = models.FloatField()  # in seconds
    stop_time = models.FloatField()  # in seconds
    is_true = models.BooleanField(default=True)

    if TYPE_CHECKING:
        video: "Video"
        raw_video: "RawVideoFile"
        label: "VideoSegmentationLabel"

    def __str__(self) -> str:
        """
        String representation of the annotation.
        """
        video_repr = self.get_video() # Get the actual video object for representation
        return f"{video_repr} - {self.label.name} - {self.start_time} to {self.stop_time}"

    def get_video(self) -> Union["Video", "RawVideoFile"]:
        """
        Get the video or raw_video associated with this annotation.

        Returns the non-null video instance (`Video` or `RawVideoFile`).
        Based on the database constraint, exactly one of these will be non-null.
        """
        if self.video:
            return self.video
        elif self.raw_video:
            return self.raw_video
        else:
            # This state should ideally not be reachable due to the CheckConstraint
            raise ValueError("Annotation is not linked to either a Video or RawVideoFile.")

    class Meta:
        constraints = [
            CheckConstraint(
                check=Q(start_time__lt=F("stop_time")),
                name="start_time_less_than_stop_time",
            ),
            CheckConstraint(
                check=(
                    Q(video__isnull=True, raw_video__isnull=False) |
                    Q(video__isnull=False, raw_video__isnull=True)
                ),
                name='exactly_one_video_or_raw_video'
            )
        ]