"""Module for annotation models"""

from .image_classification import ImageClassificationAnnotation
from .binary_classification_annotation_task import (
    BinaryClassificationAnnotationTask,
)
from .anonymized_image_annotation import (
    AnonymousImageAnnotation,
    DroppedName,
    AnonymizedImageLabel,
    AnonymizedFile,
    UploadedFile,
)
from .video_segmentation_annotation import (
    VideoSegmentationLabel,
    VideoSegmentationAnnotation,
)

from .video_segmentation_labelset import VideoSegmentationLabelSet

__all__ = [
    "ImageClassificationAnnotation",
    "BinaryClassificationAnnotationTask",
    "AnonymousImageAnnotation",
    "DroppedName",
    "AnonymizedImageLabel",
    "AnonymizedFile",
    "UploadedFile",
    "VideoSegmentationLabel",
    "VideoSegmentationLabelSet",
    "VideoSegmentationAnnotation",
]
