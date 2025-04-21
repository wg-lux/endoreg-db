from typing import TYPE_CHECKING, Union, Optional, Dict, Tuple, List
from django.db import models
from pathlib import Path
import cv2
import numpy as np
from ...utils import FRAME_DIR, FILE_STORAGE, data_paths
if TYPE_CHECKING:
    from ..video.video_file import VideoFile
    from ...label.label import Label
    from ...label.annotation import ImageClassificationAnnotation

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
        if not self.file_path.exists():
            return None
        try:
            image = cv2.imread(str(self.file_path))
            if image is None:
                pass
            return image
        except Exception as e:
            return None

    def anonymize(
        self,
        output_path: Path,
        endo_roi: Optional[List[int]] = None,
        censor_color: Tuple[int, int, int] = (0, 0, 0),
        all_black: bool = False,
    ) -> bool:
        """
        Anonymizes the frame image and saves it to output_path.
        - Applies ROI masking if endo_roi is provided.
        - Blacks out the entire frame if all_black is True.
        Returns True on success, False on failure.
        """
        image = self.get_image()
        if image is None:
            return False

        try:
            if all_black:
                anonymized_image = np.zeros_like(image)
            elif endo_roi and len(endo_roi) == 4:
                mask = np.zeros(image.shape[:2], dtype=np.uint8)
                x, y, w, h = endo_roi
                x1, y1 = max(0, x), max(0, y)
                x2, y2 = min(image.shape[1], x + w), min(image.shape[0], y + h)
                mask[y1:y2, x1:x2] = 255
                anonymized_image = cv2.bitwise_and(image, image, mask=mask)
            else:
                anonymized_image = image.copy()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            success = cv2.imwrite(str(output_path), anonymized_image)
            if not success:
                return False
            return True
        except Exception as e:
            return False

    def __str__(self):
        return f"Frame {self.frame_number} of Video {self.video.uuid}"

    def get_classification_annotations(self) -> models.QuerySet["ImageClassificationAnnotation"]:
        return self.image_classification_annotations.all()
