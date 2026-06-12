# endoreg-db/endoreg_db/services/queue/frame_annotation_queue.py

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lx_dtypes.models.contracts.frame_annotation import FrameAnnotationQueueSpecPayload

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.state.frame_annotation import (
    FrameAnnotationQueueSpec,
    normalize_frame_sampling_strategy,
    normalize_frame_task_mode,
    resolve_frame_information_source_name,
)


def _payload_from_input(
    payload: FrameAnnotationQueueSpecPayload | Mapping[str, Any],
) -> FrameAnnotationQueueSpecPayload:
    if isinstance(payload, FrameAnnotationQueueSpecPayload):
        return payload
    return FrameAnnotationQueueSpecPayload.model_validate(dict(payload))


def _resolve_label_set(label_set_id: int | None) -> LabelSet | None:
    if label_set_id is None:
        return None
    return LabelSet.objects.filter(pk=label_set_id).first()


def _resolve_label(label_id: int | None) -> Label | None:
    if label_id is None:
        return None
    return Label.objects.filter(pk=label_id).first()


def _resolve_ai_dataset(ai_dataset_id: int | None) -> AIDataSet | None:
    if ai_dataset_id is None:
        return None
    return AIDataSet.objects.filter(pk=ai_dataset_id).first()


def frame_annotation_queue_spec_from_payload(
    payload: FrameAnnotationQueueSpecPayload | Mapping[str, Any],
) -> FrameAnnotationQueueSpec:
    """
    Convert the lx_dtypes transport contract into the internal ORM-rich queue spec.

    lx_dtypes carries primitive references only. This adapter is the Django
    boundary that resolves those references to ORM objects.
    """
    resolved_payload = _payload_from_input(payload)

    return FrameAnnotationQueueSpec(
        limit=resolved_payload.limit,
        task_mode=normalize_frame_task_mode(resolved_payload.task_mode),
        video_id=resolved_payload.video_id,
        label_set=_resolve_label_set(resolved_payload.label_set_id),
        target_label=_resolve_label(resolved_payload.target_label_id),
        filter_label=_resolve_label(resolved_payload.filter_label_id),
        information_source_name=resolve_frame_information_source_name(
            resolved_payload.information_source_name
        ),
        annotator=resolved_payload.annotator,
        exclude_annotated=resolved_payload.exclude_annotated,
        ai_dataset=_resolve_ai_dataset(resolved_payload.ai_dataset_id),
        sampling_strategy=normalize_frame_sampling_strategy(
            resolved_payload.sampling_strategy
        ),
        prediction_segments_only=resolved_payload.prediction_segments_only,
        exclude_frame_ids=set(resolved_payload.exclude_frame_ids),
        require_extracted_frames=resolved_payload.require_extracted_frames,
        require_raw_video=resolved_payload.require_raw_video,
        require_processed_video=resolved_payload.require_processed_video,
        require_streamable_video_artifact=(
            resolved_payload.require_streamable_video_artifact
        ),
    )


__all__ = [
    "frame_annotation_queue_spec_from_payload",
]
