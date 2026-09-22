from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lx_dtypes.models.contracts.video_frame_annotations import (
    FrameAnnotationBulkEnvelopePayload,
    FrameAnnotationSkipPayload,
    FrameBoxAnnotationBulkEnvelopePayload,
)


def _coerce_bulk_envelope_payload(value: object) -> object:
    if not isinstance(value, list):
        return value

    annotations = cast(list[object], value)
    return {"annotations": annotations}


def _normalize_box_envelope_payload(value: object) -> object:
    envelope = _coerce_bulk_envelope_payload(value)
    if not isinstance(envelope, Mapping):
        return envelope

    mapping = cast(Mapping[str, object], envelope)
    annotations = mapping.get("annotations")
    if not isinstance(annotations, list):
        return dict(mapping)

    outer_frame_id = mapping.get("frame_id")
    outer_source = (
        mapping.get("information_source_name")
        or mapping.get("information_source")
        or "manual_annotation"
    )
    outer_annotator = mapping.get("annotator")

    normalized_annotations: list[object] = []
    for item in cast(list[object], annotations):
        if not isinstance(item, Mapping):
            normalized_annotations.append(item)
            continue
        normalized_item = dict(cast(Mapping[str, object], item))
        if normalized_item.get("frame_id") in {None, ""} and outer_frame_id is not None:
            normalized_item["frame_id"] = outer_frame_id
        if not normalized_item.get("information_source_name"):
            normalized_item["information_source_name"] = outer_source
        if (
            normalized_item.get("annotator") in {None, ""}
            and outer_annotator is not None
        ):
            normalized_item["annotator"] = outer_annotator
        normalized_annotations.append(cast(object, normalized_item))

    normalized_envelope = dict(mapping)
    normalized_envelope["annotations"] = normalized_annotations
    return normalized_envelope


def validate_frame_annotation_bulk_ingress(
    value: object,
) -> FrameAnnotationBulkEnvelopePayload:
    """Backport strict lx_dtypes mutation handling for the pinned 0.2.9 runtime."""

    envelope = _coerce_bulk_envelope_payload(value)
    return FrameAnnotationBulkEnvelopePayload.model_validate(envelope)


def validate_frame_annotation_skip_ingress(
    value: object,
) -> FrameAnnotationSkipPayload:
    """Return the sole typed skip payload after strict top-level validation."""

    return FrameAnnotationSkipPayload.model_validate(value)


def validate_frame_box_annotation_bulk_ingress(
    value: object,
) -> FrameBoxAnnotationBulkEnvelopePayload:
    """Backport the next shared box normalization without a local payload model."""

    envelope = _normalize_box_envelope_payload(value)
    return FrameBoxAnnotationBulkEnvelopePayload.model_validate(envelope)


__all__ = [
    "validate_frame_annotation_bulk_ingress",
    "validate_frame_annotation_skip_ingress",
    "validate_frame_box_annotation_bulk_ingress",
]
