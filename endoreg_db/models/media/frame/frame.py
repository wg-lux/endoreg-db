from typing import TYPE_CHECKING, Union, Optional
from django.db import models
from django.db.models import CheckConstraint
from ...utils import FRAME_DIR, FILE_STORAGE, data_paths
if TYPE_CHECKING:
    from ..video.video_file import VideoFile
    from ...label.label import Label
    from ...label.annotation import ImageClassificationAnnotation

# Unified Frame model
class Frame(models.Model):
    """
    Represents a single frame extracted from a video's raw file.

    A frame must be associated with exactly one `VideoFile`.
    """
    frame_number = models.IntegerField()
    suffix = models.CharField(max_length=255) # e.g., '.jpg', '.png'
    extracted = models.BooleanField(default=False) # Indicates if the image file exists

    # Single ForeignKey to the unified VideoFile model
    video_file = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="frames",
        null=False,
        blank=False,
    )

    def _frame_upload_path(instance, filename: str) -> str:
        """
        Group frames under `<FRAME_DIR>/<video_file.uuid>/<filename>`.
        Uses the UUID of the associated VideoFile.
        Path is relative to STORAGE_DIR.
        """
        if not instance.video_file_id:
            raise ValueError("Cannot determine upload path: Frame must be associated with a VideoFile.")

        relative_frame_dir = data_paths["frame"].relative_to(data_paths["storage"])
        upload_path = f"{relative_frame_dir}/{instance.video_file.uuid}/{filename}"
        return upload_path

    image = models.ImageField(
        upload_to=_frame_upload_path,
        storage=FILE_STORAGE,
        blank=True,
        null=True,
    )

    # Reverse relation defined in ImageClassificationAnnotation
    # image_classification_annotations: defined by related_name in ImageClassificationAnnotation

    if TYPE_CHECKING:
        video_file: "VideoFile"
        image_classification_annotations: "models.QuerySet[ImageClassificationAnnotation]"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["video_file", "frame_number"],
                name="unique_frame_per_video_file"
            ),
        ]
        indexes = [
            models.Index(fields=["video_file", "frame_number"]),
            models.Index(fields=["frame_number"]),
        ]

    def get_video(self) -> "VideoFile":
        """Returns the associated VideoFile instance."""
        if self.video_file:
            return self.video_file
        else:
            raise ValueError("Frame is not associated with a VideoFile.")

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
        try:
            video_obj = self.get_video()
            active_path = video_obj.active_file_path
            video_identifier = active_path.name if active_path else f"UUID {video_obj.uuid}"
        except ValueError:
            video_identifier = "Unknown VideoFile"
        except Exception as e:
            logger.warning("Error getting video identifier for Frame %s: %s", self.pk, e)
            video_identifier = f"VideoFile ID {self.video_file_id or 'None'}"

        return f"{video_identifier} - Frame {self.frame_number}"
