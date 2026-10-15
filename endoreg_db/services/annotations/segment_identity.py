"""Segment identity and classification boundary for frame annotations."""

from endoreg_db.models.state.frame_annotation_segment_identity import (
    MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES,
    PREDICTION_INFORMATION_SOURCE_NAMES,
    SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX,
    is_prediction_segment,
    is_segment_derived_external_annotation_id,
    manual_annotation_filter,
    manual_frame_annotation_preference_filter,
    non_segment_derived_annotation_filter,
    prediction_annotation_filter,
    segment_derived_external_annotation_id,
)


def segment_derived_external_annotation_prefix_for_segment(
    segment_id: int,
) -> str:
    return f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:{segment_id}:"


__all__ = [
    "MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES",
    "PREDICTION_INFORMATION_SOURCE_NAMES",
    "SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX",
    "is_prediction_segment",
    "is_segment_derived_external_annotation_id",
    "manual_annotation_filter",
    "manual_frame_annotation_preference_filter",
    "non_segment_derived_annotation_filter",
    "prediction_annotation_filter",
    "segment_derived_external_annotation_id",
    "segment_derived_external_annotation_prefix_for_segment",
]
