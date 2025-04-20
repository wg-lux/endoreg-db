from typing import TYPE_CHECKING, Union, Optional
from django.db import models
from django.db.models import Q, CheckConstraint
from ...utils import FRAME_DIR, FILE_STORAGE
if TYPE_CHECKING:
    from ..video import Video, RawVideoFile
    from ...label.label import Label
    from ...label.annotation import ImageClassificationAnnotation

# Unified Frame model
class Frame(models.Model):
    """
    Represents a single frame extracted from a video.

    A frame must be associated with exactly one of either a `Video` or a `RawVideoFile`.
    This exclusivity is enforced by a database constraint.
    """
    frame_number = models.IntegerField()
    suffix = models.CharField(max_length=255) # e.g., '.jpg', '.png'
    extracted = models.BooleanField(default=False) # Indicates if the image file exists

    # Conditional Foreign Keys to Video and RawVideoFile
    video = models.ForeignKey(
        "Video",
        on_delete=models.CASCADE,
        related_name="frames",
        null=True,
        blank=True,
    )
    raw_video = models.ForeignKey(
        "RawVideoFile",
        on_delete=models.CASCADE,
        related_name="frames",
        null=True,
        blank=True,
    )

    image = models.ImageField(
        upload_to=FRAME_DIR,
        storage=FILE_STORAGE,
        blank=True,
        null=True
    )

    # Reverse relation defined in ImageClassificationAnnotation
    # image_classification_annotations: defined by related_name in ImageClassificationAnnotation

    if TYPE_CHECKING:
        video: Optional["Video"]
        raw_video: Optional["RawVideoFile"]
        image_classification_annotations: "models.QuerySet[ImageClassificationAnnotation]"

    class Meta:
        constraints = [
            CheckConstraint(
            check=(
                Q(video__isnull=True, raw_video__isnull=False) |
                Q(video__isnull=False, raw_video__isnull=True)
            ),
            name='frame_exactly_one_video_or_raw_video'
            ),
            models.UniqueConstraint(
            fields=["video", "frame_number"],
            condition=Q(video__isnull=False),
            name="unique_frame_per_video"
            ),
            models.UniqueConstraint(
            fields=["raw_video", "frame_number"],
            condition=Q(raw_video__isnull=False),
            name="unique_frame_per_raw_video"
            ),
        ]
        indexes = [
            models.Index(fields=["video", "frame_number"]),
            models.Index(fields=["raw_video", "frame_number"]),
            models.Index(fields=["frame_number"]),
        ]

    def get_video(self) -> Union["Video", "RawVideoFile"]:
        """Returns the associated Video or RawVideoFile instance."""
        if self.video:
            return self.video
        elif self.raw_video:
            return self.raw_video
        else:
            raise ValueError("Frame has neither video nor raw_video set.")

    def get_classification_annotations(self):
        """Get all image classification annotations for this frame."""
        return self.image_classification_annotations.all()

    def get_classification_annotations_by_label(self, label: "Label"):
        """Get all image classification annotations for this frame with the given label."""
        return self.image_classification_annotations.filter(label=label)

    def get_classification_annotations_by_value(self, value: bool):
        """Get all image classification annotations for this frame with the given value."""
        return self.image_classification_annotations.filter(value=value)

    def get_classification_annotations_by_label_and_value(
        self, label: "Label", value: bool
    ):
        """Get all image classification annotations for this frame with the given label and value."""
        return self.image_classification_annotations.filter(label=label, value=value)

    def __str__(self):
        video_obj = self.get_video()
        video_path = getattr(getattr(video_obj, 'file', None), 'path', str(video_obj.id))
        return f"{video_path} - Frame {self.frame_number}"
