"""Module for annotation models"""

from .frame_box import FrameBoxAnnotation
from .image_classification import ImageClassificationAnnotation

from .video_segmentation_annotation import (
    VideoSegmentationAnnotation,
)

__all__ = [
    "FrameBoxAnnotation",
    "ImageClassificationAnnotation",
    "VideoSegmentationAnnotation",
]
