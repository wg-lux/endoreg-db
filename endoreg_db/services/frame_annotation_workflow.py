from __future__ import annotations

from typing import cast

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.state.frame_annotation import (
    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
    SUPPORTED_FRAME_SAMPLING_STRATEGIES,
    SUPPORTED_FRAME_TASK_MODES,
    FrameAnnotationQueueResult,
    FrameAnnotationQueueSpec,
    FrameAnnotationTaskPayload,
    FrameSamplingStrategy,
    FrameTaskMode,
    RequestLike,
)
from endoreg_db.models.state.frame_annotation import (
    ai_dataset_requires_raw_frames as _ai_dataset_requires_raw_frames,
)
from endoreg_db.models.state.frame_annotation import (
    build_frame_task_queue as _build_frame_task_queue,
)
from endoreg_db.models.state.frame_annotation import (
    normalize_frame_sampling_strategy as _normalize_frame_sampling_strategy,
)
from endoreg_db.models.state.frame_annotation import (
    normalize_frame_task_mode as _normalize_frame_task_mode,
)
from endoreg_db.models.state.frame_annotation import (
    resolve_ai_dataset_for_queue as _resolve_ai_dataset_for_queue,
)
from endoreg_db.models.state.frame_annotation import (
    resolve_frame_information_source_name as _resolve_frame_information_source_name,
)
from endoreg_db.models.state.frame_annotation import (
    resolve_request_annotator as _resolve_request_annotator,
)


def normalize_frame_task_mode(value: object) -> FrameTaskMode:
    return _normalize_frame_task_mode(value)


def normalize_frame_sampling_strategy(value: object) -> FrameSamplingStrategy:
    return _normalize_frame_sampling_strategy(value)


def resolve_request_annotator(
    request: object,
    requested_annotator: str | None = None,
) -> str:
    return _resolve_request_annotator(
        cast(RequestLike, request),
        requested_annotator,
    )


def resolve_frame_information_source_name(value: object) -> str:
    return _resolve_frame_information_source_name(value)


def resolve_ai_dataset_for_queue(
    *,
    dataset_name_raw: object,
    dataset_type_raw: object,
    dataset_id_raw: object = None,
) -> AIDataSet | None:
    return _resolve_ai_dataset_for_queue(
        dataset_name_raw=dataset_name_raw,
        dataset_type_raw=dataset_type_raw,
        dataset_id_raw=dataset_id_raw,
    )


def ai_dataset_requires_raw_frames(dataset: AIDataSet | None) -> bool:
    return _ai_dataset_requires_raw_frames(dataset)


def build_frame_task_queue(
    queue_spec: FrameAnnotationQueueSpec,
) -> FrameAnnotationQueueResult:
    return _build_frame_task_queue(queue_spec)


__all__ = [
    "DEFAULT_FRAME_INFORMATION_SOURCE_NAME",
    "SUPPORTED_FRAME_SAMPLING_STRATEGIES",
    "SUPPORTED_FRAME_TASK_MODES",
    "FrameAnnotationTaskPayload",
    "ai_dataset_requires_raw_frames",
    "build_frame_task_queue",
    "normalize_frame_sampling_strategy",
    "normalize_frame_task_mode",
    "resolve_ai_dataset_for_queue",
    "resolve_frame_information_source_name",
    "resolve_request_annotator",
]
