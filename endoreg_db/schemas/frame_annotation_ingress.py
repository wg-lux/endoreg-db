from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lx_dtypes.models.contracts.video_frame_annotations import (
    FrameAnnotationBulkEnvelopePayload,
    FrameAnnotationSkipPayload,
    FrameBoxAnnotationBulkEnvelopePayload,
)


_BULK_ENVELOPE_FIELDS = frozenset({"video_id", "ai_dataset_id", "annotations"})
_SKIP_FIELDS = frozenset(
    {
        "frame_id",
        "video_id",
        "annotator",
        "reason",
        "information_source_name",
        "information_source",
        "exclude_annotated",
    }
)
_ITEM_FIELDS = frozenset(
    {
        "frame_id",
        "label_id",
        "choice_name",
        "value",
        "float_value",
        "information_source_name",
        "annotator",
        "external_annotation_id",
        "model_meta_id",
    }
)
_BOX_ENVELOPE_FIELDS = frozenset(
    {
        "frame_id",
        "video_id",
        "replace",
        "annotator",
        "information_source_name",
        "information_source",
        "annotations",
    }
)
_BOX_ITEM_FIELDS = _ITEM_FIELDS | {
    "id",
    "x",
    "y",
    "width",
    "height",
    "image_width",
    "image_height",
}
_DEFAULT_INFORMATION_SOURCE = "manual_annotation"


def _reject_unknown_fields(
    value: Mapping[object, object],
    *,
    allowed: frozenset[str],
    path: str,
) -> None:
    unknown = sorted(str(key) for key in value if str(key) not in allowed)
    if unknown:
        raise ValueError(f"Unknown field(s) at {path}: {', '.join(unknown)}")


def _reject_unknown_items(
    annotations: object,
    *,
    allowed: frozenset[str],
) -> None:
    if not isinstance(annotations, list):
        return
    for index, item in enumerate(cast(list[object], annotations)):
        if isinstance(item, Mapping):
            _reject_unknown_fields(
                cast(Mapping[object, object], item),
                allowed=allowed,
                path=f"annotations.{index}",
            )


def validate_frame_annotation_bulk_ingress(
    value: object,
) -> FrameAnnotationBulkEnvelopePayload:
    """Backport strict lx_dtypes mutation handling for the pinned 0.2.9 runtime."""

    envelope: object = (
        {"annotations": cast(list[object], value)}
        if isinstance(value, list)
        else value
    )
    if isinstance(envelope, Mapping):
        mapping = cast(Mapping[object, object], envelope)
        _reject_unknown_fields(
            mapping,
            allowed=_BULK_ENVELOPE_FIELDS,
            path="payload",
        )
        _reject_unknown_items(mapping.get("annotations"), allowed=_ITEM_FIELDS)
    return FrameAnnotationBulkEnvelopePayload.model_validate(envelope)


def validate_frame_annotation_skip_ingress(
    value: object,
) -> FrameAnnotationSkipPayload:
    """Return the sole typed skip payload after strict top-level validation."""

    if isinstance(value, Mapping):
        _reject_unknown_fields(
            cast(Mapping[object, object], value),
            allowed=_SKIP_FIELDS,
            path="payload",
        )
    return FrameAnnotationSkipPayload.model_validate(value)


def validate_frame_box_annotation_bulk_ingress(
    value: object,
) -> FrameBoxAnnotationBulkEnvelopePayload:
    """Backport the next shared box normalization without a local payload model."""

    envelope: object = (
        {"annotations": cast(list[object], value)}
        if isinstance(value, list)
        else value
    )
    if not isinstance(envelope, Mapping):
        return FrameBoxAnnotationBulkEnvelopePayload.model_validate(envelope)

    mapping = cast(Mapping[object, object], envelope)
    _reject_unknown_fields(
        mapping,
        allowed=_BOX_ENVELOPE_FIELDS,
        path="payload",
    )
    annotations = mapping.get("annotations")
    _reject_unknown_items(annotations, allowed=_BOX_ITEM_FIELDS)
    if not isinstance(annotations, list):
        return FrameBoxAnnotationBulkEnvelopePayload.model_validate(envelope)

    outer_frame_id = mapping.get("frame_id")
    outer_source = (
        mapping.get("information_source_name")
        or mapping.get("information_source")
        or _DEFAULT_INFORMATION_SOURCE
    )
    outer_annotator = mapping.get("annotator")
    normalized_items: list[object] = []
    for item in cast(list[object], annotations):
        if not isinstance(item, Mapping):
            normalized_items.append(item)
            continue
        normalized_item = dict(cast(Mapping[object, object], item))
        if normalized_item.get("frame_id") in {None, ""} and outer_frame_id is not None:
            normalized_item["frame_id"] = outer_frame_id
        if not normalized_item.get("information_source_name"):
            normalized_item["information_source_name"] = outer_source
        if normalized_item.get("annotator") in {None, ""} and outer_annotator is not None:
            normalized_item["annotator"] = outer_annotator
        normalized_items.append(normalized_item)

    normalized_envelope = dict(mapping)
    normalized_envelope["annotations"] = normalized_items
    payload = FrameBoxAnnotationBulkEnvelopePayload.model_validate(normalized_envelope)
    if payload.frame_id is not None and any(
        item.frame_id != payload.frame_id for item in payload.annotations
    ):
        raise ValueError("annotations frame_id must match the envelope frame_id")
    return payload


__all__ = [
    "validate_frame_annotation_bulk_ingress",
    "validate_frame_annotation_skip_ingress",
    "validate_frame_box_annotation_bulk_ingress",
]
