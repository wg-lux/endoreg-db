"""Module for annotation models"""

from .image_classification import ImageClassificationAnnotation

from .video_segmentation_annotation import (
    VideoSegmentationAnnotation,
)

__all__ = [
    "ImageClassificationAnnotation",
    "VideoSegmentationAnnotation",
]
