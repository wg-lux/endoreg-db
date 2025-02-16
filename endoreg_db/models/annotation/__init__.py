'''Module for annotation models'''
from .image_classification import (
    ImageClassificationAnnotation
)
from .binary_classification_annotation_task import (
    LegacyBinaryClassificationAnnotationTask,
    BinaryClassificationAnnotationTask
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

__all__ = [
    "ImageClassificationAnnotation",
    "LegacyBinaryClassificationAnnotationTask",
    "BinaryClassificationAnnotationTask",
    "AnonymousImageAnnotation",
    "DroppedName",
    "AnonymizedImageLabel",
    "AnonymizedFile",
    "UploadedFile",
    "VideoSegmentationLabel",
    "VideoSegmentationAnnotation",
]

