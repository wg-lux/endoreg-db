from __future__ import annotations

import logging
from pathlib import Path
from types import NoneType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast, Any

import cv2
import numpy as np
from django.db import models

if TYPE_CHECKING:
    from endoreg_db.models.label.annotation.image_classification import (
        ImageClassificationAnnotation,
    )

    class FrameVideoCarrier(Protocol):
        video_hash: str

        def get_frame_dir_path(self) -> Path | None: ...


NoFrameTimestampValue: TypeAlias = NoneType
FrameTimestamp: TypeAlias = "float | NoFrameTimestampValue"
FrameImage: TypeAlias = "np.ndarray | NoFrameTimestampValue"

logger = logging.getLogger(__name__)


# Unified Frame model
class Frame(models.Model):
    video: models.ForeignKey[Any] = models.ForeignKey(
        "VideoFile",
        on_delete=models.CASCADE,
        related_name="frames",
        blank=False,
        null=False,
    )
    frame_number: models.PositiveIntegerField[Any, Any] = models.PositiveIntegerField()
    relative_path: models.CharField[Any, Any] = models.CharField(max_length=512)
    timestamp: models.FloatField[Any, Any] = models.FloatField(null=True, blank=True)
    presentation_timestamp: models.BigIntegerField[Any, Any] = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Exact presentation timestamp tick in the selected video stream time base.",
    )

    is_extracted: models.BooleanField[Any, Any] = models.BooleanField(default=False)

    if TYPE_CHECKING:
        image_classification_annotations: models.QuerySet[
            "ImageClassificationAnnotation"
        ]

    class Meta:
        unique_together = ("video", "frame_number")
        ordering = ["video", "frame_number"]
        indexes = [
            models.Index(
                fields=["video", "timestamp"],
                name="frame_video_timestamp_idx",
            )
        ]

    @property
    def file_path(self) -> Path:
        """
        Return the absolute filesystem path to the frame image by combining the video's frame directory with the frame's relative path.

        Returns:
            Path: The absolute path to the frame image file.
        """
        video = cast("FrameVideoCarrier", self.video)
        base_dir = video.get_frame_dir_path()
        assert base_dir is not None, "Video frame directory path should not be None"
        return base_dir / self.relative_path

    @property
    def predictions(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return all image classification annotations for this frame that are linked to an information source of type "prediction".

        Returns:
            QuerySet: A queryset of related ImageClassificationAnnotation objects filtered to those whose information source type is "prediction".
        """
        from endoreg_db.models.state.frame_annotation import (
            prediction_annotation_filter,
        )

        return self.image_classification_annotations.filter(
            prediction_annotation_filter()
        )

    @property
    def manual_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        """
        Return all manual image classification annotations associated with this frame.

        Returns:
            QuerySet: A queryset of related ImageClassificationAnnotation objects whose information source type is "manual_annotation".
        """
        from endoreg_db.models.state.frame_annotation import manual_annotation_filter

        return self.image_classification_annotations.filter(manual_annotation_filter())

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

    def get_image(self) -> FrameImage:
        """
        Load and return the frame image as a NumPy array using OpenCV.

        Returns:
            The image as a NumPy array if successfully loaded, or None if the file does not exist or cannot be read.
        """
        frame_path = self.file_path
        video = cast("FrameVideoCarrier", self.video)
        if not frame_path.exists():
            logger.warning(
                "Frame file not found at %s for Frame %s (Video %s)",
                frame_path,
                self.pk,
                video.video_hash,
            )
            return None
        try:
            image = cv2.imread(str(frame_path))
            if image is None:
                logger.warning(
                    "cv2.imread returned None for frame file %s (Frame %s, Video %s)",
                    frame_path,
                    self.pk,
                    video.video_hash,
                )
            return image
        except Exception as e:
            logger.error(
                "Error reading frame file %s (Frame %s, Video %s): %s",
                frame_path,
                self.pk,
                video.video_hash,
                e,
                exc_info=True,
            )
            return None

    def __str__(self) -> str:
        video = cast("FrameVideoCarrier", self.video)
        return f"Frame {self.frame_number} of Video {video.video_hash}"

    def get_classification_annotations(
        self,
    ) -> models.QuerySet["ImageClassificationAnnotation"]:
        return self.image_classification_annotations.all()
