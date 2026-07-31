from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.state.frame_annotation import (
    DEFAULT_FRAME_INFORMATION_SOURCE_NAME,
    SUPPORTED_FRAME_SAMPLING_STRATEGIES,
    SUPPORTED_FRAME_TASK_MODES,
    FrameAnnotationQueueResult,
    FrameAnnotationQueueSpec,
    FrameAnnotationTaskPayload,
    FrameLike,
    FrameSamplingStrategy,
    FrameTaskMode,
    RequestLike,
    build_annotation_frame_buckets,
    build_balanced_label_order,
    build_dataset_candidate_frame_ids,
    build_dataset_label_distribution,
    build_dataset_target_buckets,
    build_segment_frame_buckets,
    mark_frame_prediction_completed,
    mark_frame_prediction_reset,
    mark_prediction_segments_created,
    merge_frame_buckets,
    pick_balanced_dataset_frame,
    pick_random_frame,
    serialize_frame_task,
    serialize_label_distribution,
)
from endoreg_db.models.state.frame_annotation import (
    ai_dataset_requires_raw_frames as _ai_dataset_requires_raw_frames,
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
from endoreg_db.models.state.frame_annotation import (
    validated_annotators_for_video as _validated_annotators_for_video,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


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


def validated_annotators_for_video(video: VideoFile) -> list[str]:
    return _validated_annotators_for_video(video)


def build_frame_task_queue(
    queue_spec: FrameAnnotationQueueSpec,
) -> FrameAnnotationQueueResult:
    queue_inputs = _build_queue_inputs(queue_spec)
    queue_state = _QueueState(excluded_ids=set(queue_spec.exclude_frame_ids))
    _select_balanced_frames(queue_spec, queue_inputs, queue_state)
    _select_target_bucket_frames(queue_spec, queue_inputs, queue_state)
    _fill_queue_randomly(queue_spec, queue_inputs, queue_state)
    return _queue_result(queue_inputs, queue_state)


@dataclass(frozen=True)
class _QueueInputs:
    dataset_buckets: dict[str, set[int]]
    label_distribution: dict[int, dict[str, int]]
    balanced_label_order: list[int]
    segment_frame_buckets: dict[int, set[int]]
    annotation_frame_buckets: dict[int, set[int]]
    balanced_frame_buckets: dict[int, set[int]]
    dataset_candidate_frame_ids: set[int] | None


@dataclass
class _QueueState:
    tasks: list[FrameAnnotationTaskPayload] = field(default_factory=lambda: [])
    excluded_ids: set[int] = field(default_factory=lambda: set())
    selected_label_counts: Counter[int] = field(default_factory=lambda: Counter[int]())
    selection_strategy: str = "random"


def _build_queue_inputs(spec: FrameAnnotationQueueSpec) -> _QueueInputs:
    dataset_buckets = build_dataset_target_buckets(
        dataset=spec.ai_dataset,
        target_label=spec.target_label,
        require_extracted_frames=spec.require_extracted_frames,
    )
    label_distribution = build_dataset_label_distribution(
        dataset=spec.ai_dataset,
        label_set=spec.label_set,
    )
    segment_frame_buckets = _requested_segment_buckets(spec)
    annotation_frame_buckets = _requested_annotation_buckets(spec)
    return _QueueInputs(
        dataset_buckets=dataset_buckets,
        label_distribution=label_distribution,
        balanced_label_order=build_balanced_label_order(
            label_set=spec.label_set,
            target_label=spec.target_label,
            distribution=label_distribution,
        ),
        segment_frame_buckets=segment_frame_buckets,
        annotation_frame_buckets=annotation_frame_buckets,
        balanced_frame_buckets=merge_frame_buckets(
            segment_frame_buckets,
            annotation_frame_buckets,
        ),
        dataset_candidate_frame_ids=build_dataset_candidate_frame_ids(
            dataset=spec.ai_dataset,
            label_set=spec.label_set,
            only_prediction_segments=spec.prediction_segments_only,
            require_extracted_frames=spec.require_extracted_frames,
        ),
    )


def _requested_segment_buckets(
    spec: FrameAnnotationQueueSpec,
) -> dict[int, set[int]]:
    if spec.sampling_strategy not in {
        FrameSamplingStrategy.BALANCED,
        FrameSamplingStrategy.SEGMENTS,
    }:
        return {}
    return build_segment_frame_buckets(
        dataset=spec.ai_dataset,
        label_set=spec.label_set,
        only_prediction_segments=spec.prediction_segments_only,
        require_extracted_frames=spec.require_extracted_frames,
    )


def _requested_annotation_buckets(
    spec: FrameAnnotationQueueSpec,
) -> dict[int, set[int]]:
    if spec.sampling_strategy not in {
        FrameSamplingStrategy.BALANCED,
        FrameSamplingStrategy.ANNOTATIONS,
    }:
        return {}
    return build_annotation_frame_buckets(
        dataset=spec.ai_dataset,
        label_set=spec.label_set,
        require_extracted_frames=spec.require_extracted_frames,
    )


def _uses_balanced_frame_buckets(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
) -> bool:
    uses_target_bucket_order = bool(
        queue_inputs.dataset_buckets
        and spec.sampling_strategy == FrameSamplingStrategy.BALANCED
    )
    return bool(
        spec.sampling_strategy != FrameSamplingStrategy.NONE
        and queue_inputs.balanced_frame_buckets
        and not uses_target_bucket_order
    )


def _select_balanced_frames(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
) -> None:
    if not _uses_balanced_frame_buckets(spec, queue_inputs):
        return
    queue_state.selection_strategy = f"dataset_{spec.sampling_strategy.value}"
    while len(queue_state.tasks) < spec.limit:
        frame, label_id = _next_balanced_frame(spec, queue_inputs, queue_state)
        if frame is None:
            return
        task = serialize_frame_task(frame, spec=spec)
        task = _with_selection_label(task, label_id, queue_state)
        task = _with_dataset_bucket(task, frame, queue_inputs.dataset_buckets)
        task = task.model_copy(
            update={"dataset_selection_source": spec.sampling_strategy.value}
        )
        _append_task(queue_state, frame, task)


def _next_balanced_frame(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
) -> tuple[FrameLike | None, int | None]:
    label_order = sorted(
        queue_inputs.balanced_label_order,
        key=lambda label_id: (
            queue_state.selected_label_counts[label_id],
            queue_inputs.label_distribution.get(label_id, {}).get("total", 0),
            label_id,
        ),
    )
    return pick_balanced_dataset_frame(
        spec=spec,
        label_order=label_order,
        frame_buckets=queue_inputs.balanced_frame_buckets,
        exclude_frame_ids=queue_state.excluded_ids,
    )


def _with_selection_label(
    task: FrameAnnotationTaskPayload,
    label_id: int | None,
    queue_state: _QueueState,
) -> FrameAnnotationTaskPayload:
    if label_id is None:
        return task
    queue_state.selected_label_counts[label_id] += 1
    return task.model_copy(
        update={
            "dataset_selection_label_id": label_id,
            "dataset_selection_label_name": str(label_id),
        }
    )


def _frame_id(frame: FrameLike) -> int:
    return cast(int, getattr(frame, "pk", getattr(frame, "id")))


def _with_dataset_bucket(
    task: FrameAnnotationTaskPayload,
    frame: FrameLike,
    dataset_buckets: dict[str, set[int]],
) -> FrameAnnotationTaskPayload:
    frame_id = _frame_id(frame)
    for bucket_name, bucket_frame_ids in dataset_buckets.items():
        if frame_id in bucket_frame_ids:
            return task.model_copy(update={"dataset_bucket": bucket_name})
    return task


def _append_task(
    queue_state: _QueueState,
    frame: FrameLike,
    task: FrameAnnotationTaskPayload,
) -> None:
    queue_state.tasks.append(task)
    queue_state.excluded_ids.add(_frame_id(frame))


def _select_target_bucket_frames(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
) -> None:
    if not queue_inputs.dataset_buckets:
        return
    bucket_order = ("positive", "negative", "unknown")
    while len(queue_state.tasks) < spec.limit:
        if not _select_target_bucket_pass(
            spec, queue_inputs, queue_state, bucket_order
        ):
            return


def _select_target_bucket_pass(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
    bucket_order: tuple[str, ...],
) -> bool:
    previous_task_count = len(queue_state.tasks)
    for bucket_name in bucket_order:
        _append_from_target_bucket(spec, queue_inputs, queue_state, bucket_name)
        if len(queue_state.tasks) >= spec.limit:
            break
    return len(queue_state.tasks) > previous_task_count


def _append_from_target_bucket(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
    bucket_name: str,
) -> None:
    bucket_frame_ids = queue_inputs.dataset_buckets.get(bucket_name)
    if not bucket_frame_ids:
        return
    frame = pick_random_frame(
        spec=spec,
        exclude_frame_ids=queue_state.excluded_ids,
        candidate_frame_ids=bucket_frame_ids,
    )
    if frame is None:
        return
    task = serialize_frame_task(frame, spec=spec).model_copy(
        update={"dataset_bucket": bucket_name}
    )
    _append_task(queue_state, frame, task)


def _fill_queue_randomly(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
) -> None:
    candidate_frame_ids = _random_candidate_frame_ids(queue_inputs)
    while len(queue_state.tasks) < spec.limit:
        frame = pick_random_frame(
            spec=spec,
            exclude_frame_ids=queue_state.excluded_ids,
            candidate_frame_ids=candidate_frame_ids,
        )
        frame = _retry_without_bucket_limit(spec, queue_inputs, queue_state, frame)
        if frame is None:
            return
        task = _with_dataset_bucket(
            serialize_frame_task(frame, spec=spec),
            frame,
            queue_inputs.dataset_buckets,
        )
        _append_task(queue_state, frame, task)


def _random_candidate_frame_ids(queue_inputs: _QueueInputs) -> set[int] | None:
    if queue_inputs.dataset_candidate_frame_ids is not None:
        return queue_inputs.dataset_candidate_frame_ids
    if not queue_inputs.dataset_buckets:
        return None
    frame_ids: set[int] = set()
    for bucket_frame_ids in queue_inputs.dataset_buckets.values():
        frame_ids.update(bucket_frame_ids)
    return frame_ids


def _retry_without_bucket_limit(
    spec: FrameAnnotationQueueSpec,
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
    frame: FrameLike | None,
) -> FrameLike | None:
    if frame is not None:
        return frame
    if (
        not queue_inputs.dataset_buckets
        or queue_inputs.dataset_candidate_frame_ids is not None
    ):
        return None
    return pick_random_frame(
        spec=spec,
        exclude_frame_ids=queue_state.excluded_ids,
    )


def _queue_result(
    queue_inputs: _QueueInputs,
    queue_state: _QueueState,
) -> FrameAnnotationQueueResult:
    return FrameAnnotationQueueResult(
        tasks=queue_state.tasks,
        selection_strategy=queue_state.selection_strategy,
        label_distribution=serialize_label_distribution(
            queue_inputs.label_distribution
        ),
        selected_label_counts={
            str(label_id): count
            for label_id, count in queue_state.selected_label_counts.items()
        },
        segment_bucket_counts=_bucket_counts(queue_inputs.segment_frame_buckets),
        annotation_bucket_counts=_bucket_counts(queue_inputs.annotation_frame_buckets),
        bucket_counts={
            bucket_name: len(frame_ids)
            for bucket_name, frame_ids in queue_inputs.dataset_buckets.items()
        },
    )


def _bucket_counts(frame_buckets: dict[int, set[int]]) -> dict[str, int]:
    return {
        str(label_id): len(frame_ids) for label_id, frame_ids in frame_buckets.items()
    }


__all__ = [
    "DEFAULT_FRAME_INFORMATION_SOURCE_NAME",
    "SUPPORTED_FRAME_SAMPLING_STRATEGIES",
    "SUPPORTED_FRAME_TASK_MODES",
    "FrameAnnotationQueueResult",
    "FrameAnnotationQueueSpec",
    "FrameAnnotationTaskPayload",
    "ai_dataset_requires_raw_frames",
    "build_frame_task_queue",
    "mark_frame_prediction_completed",
    "mark_frame_prediction_reset",
    "mark_prediction_segments_created",
    "normalize_frame_sampling_strategy",
    "normalize_frame_task_mode",
    "resolve_ai_dataset_for_queue",
    "resolve_frame_information_source_name",
    "resolve_request_annotator",
    "validated_annotators_for_video",
]
