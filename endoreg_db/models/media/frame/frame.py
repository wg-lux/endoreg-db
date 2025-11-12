import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np
from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models import ImageClassificationAnnotation, VideoFile

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
        video: models.ForeignKey["VideoFile"]

    class Meta:
        unique_together = ("video", "frame_number")
        ordering = ["video", "frame_number"]

    @property
    def file_path(self) -> Path:
        """
        Return the absolute filesystem path to the frame image by combining the video's frame directory with the frame's relative path.

        Returns:
            Path: The absolute path to the frame image file.
        """
        base_dir = self.video.get_frame_dir_path()
        assert base_dir is not None, "Video frame directory path should not be None"
        return base_dir / self.relative_path

    @property
    def predictions(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return all image classification annotations for this frame that are linked to an information source of type "prediction".

        Returns:
            QuerySet: A queryset of related ImageClassificationAnnotation objects filtered to those whose information source type is "prediction".
        """
        return self.image_classification_annotations.filter(information_source__information_source_types__name="prediction")

    @property
    def manual_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return all manual image classification annotations associated with this frame.

        Returns:
            QuerySet: A queryset of related ImageClassificationAnnotation objects whose information source type is "manual_annotation".
        """
        return self.image_classification_annotations.filter(information_source__information_source_types__name="manual_annotation")

    @property
    def has_predictions(self) -> bool:
        """
        Returns True if the frame has any associated prediction annotations.

        A prediction annotation is defined as an ImageClassificationAnnotation whose information source type is "prediction".
        """
        return self.predictions.exists()

    @property
    def has_manual_annotations(self) -> bool:
        """
        Returns True if the frame has any manual image classification annotations.

        Manual annotations are identified as related ImageClassificationAnnotation objects whose information source type is named "manual_annotation".
        """
        return self.manual_annotations.exists()

    def get_image(self) -> Optional[np.ndarray]:
        """
        Load and return the frame image as a NumPy array using OpenCV.

        Returns:
            The image as a NumPy array if successfully loaded, or None if the file does not exist or cannot be read.
        """
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
