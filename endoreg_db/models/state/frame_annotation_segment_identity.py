"""Persistence-facing segment identity and annotation query predicates."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol, cast

from django.db.models import Q

SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX = "segment-derived:v1"

PREDICTION_INFORMATION_SOURCE_NAMES = {
    "prediction",
    "default_prediction",
    "prediction_annotation",
}

MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES = {
    "annotation",
    "default_annotation",
    "frame_annotation_frontend",
    "human_annotation",
    "lx_anonymizer_evaluation",
    "manual_annotation",
}


class _FrameAnnotationSourceName(Protocol):
    name: str


def is_prediction_segment(segment: object) -> bool:
    source = cast(_FrameAnnotationSourceName | None, getattr(segment, "source", None))
    source_name = (source.name if source else "").strip().lower()
    prediction_meta_id = getattr(segment, "prediction_meta_id", None)
    return (
        prediction_meta_id is not None
        or source_name in PREDICTION_INFORMATION_SOURCE_NAMES
        or source_name.startswith("prediction")
        or source_name.startswith("model")
    )


def segment_derived_external_annotation_id(
    *,
    segment_id: int | None,
    frame_id: int | None,
    label_id: int | None,
    information_source_id: int | None,
    model_meta_id: int | None,
    annotator: str | None = None,
) -> str:
    normalized_parts = [
        str(segment_id or ""),
        str(frame_id or ""),
        str(label_id or ""),
        str(information_source_id or ""),
        str(model_meta_id or ""),
        str(annotator or ""),
    ]
    digest = sha256("|".join(normalized_parts).encode("utf-8")).hexdigest()[:24]
    return (
        f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
        f"{segment_id or 'none'}:{frame_id or 'none'}:{digest}"
    )


def is_segment_derived_external_annotation_id(value: object) -> bool:
    return isinstance(value, str) and value.startswith(
        f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
    )


def non_segment_derived_annotation_filter() -> Q:
    return (
        Q(external_annotation_id__isnull=True)
        | Q(external_annotation_id__exact="")
        | ~Q(
            external_annotation_id__startswith=(
                f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
            )
        )
    )


def prediction_annotation_filter() -> Q:
    return (
        Q(information_source__information_source_types__name="prediction")
        | Q(information_source__name__in=PREDICTION_INFORMATION_SOURCE_NAMES)
        | Q(model_meta_id__isnull=False)
    )


def manual_annotation_filter(
    information_source_name: str | None = None,
) -> Q:
    if information_source_name:
        return Q(information_source__name=information_source_name)
    return Q(
        information_source__information_source_types__name__in=[
            "annotation",
            "manual_annotation",
        ]
    ) | Q(information_source__name__in=MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES)


def manual_frame_annotation_preference_filter() -> Q:
    return manual_annotation_filter() & non_segment_derived_annotation_filter()


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
]
