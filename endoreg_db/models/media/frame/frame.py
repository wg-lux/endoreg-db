import logging
from typing import TYPE_CHECKING, Optional
from django.db import models
from pathlib import Path
import cv2
import numpy as np
if TYPE_CHECKING:
    from endoreg_db.models import Label, ImageClassificationAnnotation, VideoFile

logger = logging.getLogger(__name__)

# Unified Frame model
class Frame(models.Model):
    video = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="frames",
        blank=False,
        null=False,
    )
    frame_number = models.PositiveIntegerField()
    relative_path = models.CharField(max_length=512)
    timestamp = models.FloatField(null=True, blank=True)
    is_extracted = models.BooleanField(default=False)

    if TYPE_CHECKING:
        image_classification_annotations: models.QuerySet["ImageClassificationAnnotation"]
        labels: models.QuerySet["Label"]
        video: "VideoFile"
    class Meta:
        unique_together = ('video', 'frame_number')
        ordering = ['video', 'frame_number']

    @property
    def file_path(self) -> Path:
        """Returns the absolute path to the frame file."""
        base_dir = self.video.get_frame_dir_path()
        return base_dir / self.relative_path

    def get_image(self) -> Optional[np.ndarray]:
        """Reads and returns the frame image using OpenCV."""
        frame_path = self.file_path
        if not frame_path.exists():
            logger.warning("Frame file not found at %s for Frame %s (Video %s)", frame_path, self.pk, self.video.uuid)
            return None
        try:
            image = cv2.imread(str(frame_path))
            if image is None:
                logger.warning("cv2.imread returned None for frame file %s (Frame %s, Video %s)", frame_path, self.pk, self.video.uuid)
            return image
        except Exception as e:
            logger.error("Error reading frame file %s (Frame %s, Video %s): %s", frame_path, self.pk, self.video.uuid, e, exc_info=True)
            return None

    def __str__(self):
        return f"Frame {self.frame_number} of Video {self.video.uuid}"

    def get_classification_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        return self.image_classification_annotations.all()
