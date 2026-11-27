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

    # Compatibility for pinned lx-dtypes: empty legacy values mean omitted.
    # Shared validators own inheritance, defaults and cross-frame checks.
    normalized_envelope = dict(mapping)
    for key in ("annotator", "information_source_name", "information_source"):
        if normalized_envelope.get(key) is None:
            normalized_envelope.pop(key, None)
    normalized_annotations: list[object] = []
    for item in cast(list[object], annotations):
        if isinstance(item, Mapping):
            normalized_item = dict(cast(Mapping[str, object], item))
            for key in ("frame_id", "information_source_name", "annotator"):
                if normalized_item.get(key) in (None, ""):
                    normalized_item.pop(key, None)
            normalized_annotations.append(normalized_item)
        else:
            normalized_annotations.append(item)
    normalized_envelope["annotations"] = normalized_annotations
    return normalized_envelope


def validate_frame_annotation_bulk_ingress(
    value: object,
) -> FrameAnnotationBulkEnvelopePayload:
    """Accept the legacy list envelope and validate the shared contract."""

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
    """Normalize legacy omissions before shared box-envelope validation."""

    envelope = _normalize_box_envelope_payload(value)
    return FrameBoxAnnotationBulkEnvelopePayload.model_validate(envelope)


__all__ = [
    "validate_frame_annotation_bulk_ingress",
    "validate_frame_annotation_skip_ingress",
    "validate_frame_box_annotation_bulk_ingress",
]
