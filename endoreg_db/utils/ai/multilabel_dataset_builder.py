"""
Build image multi-label training payloads from an AIDataSet boundary.

The public source-scope contract is:

* ``all``: include frame annotations and segment annotations attached to the
  selected AIDataSet.
* ``frame_only``: include only ImageClassificationAnnotation rows attached to
  the selected AIDataSet.
* ``segment_only``: include only LabelVideoSegment rows attached to the selected
  AIDataSet, expanded in memory to existing Frame rows.

Segment-derived labels are never persisted as ImageClassificationAnnotation
rows. The selected AIDataSet remains the canonical training boundary for every
scope.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict, cast

from django.db import models

from endoreg_db.models.aidataset.aidataset import AIDataSet
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.label.label_set import LabelSet
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame

from endoreg_db.services.aidataset_training_selection import (
    ANNOTATION_SOURCE_SCOPE_ALL as ANNOTATION_SOURCE_SCOPE_ALL,
    ANNOTATION_SOURCE_SCOPE_FRAME_ONLY as ANNOTATION_SOURCE_SCOPE_FRAME_ONLY,
    ANNOTATION_SOURCE_SCOPE_SEGMENT_ONLY as ANNOTATION_SOURCE_SCOPE_SEGMENT_ONLY,
    VALID_ANNOTATION_SOURCE_SCOPES as VALID_ANNOTATION_SOURCE_SCOPES,
    AnnotationSourceScope,
    normalize_annotation_source_scope as normalize_annotation_source_scope,
    frames_for_training_segments,
    infer_training_labelset,
    training_annotation_querysets,
    uses_frame_annotations as uses_frame_annotations,
    uses_segment_annotations as uses_segment_annotations,
)


class ImageMultilabelDataset(TypedDict):
    """
    In-memory representation of an image multi-label training dataset.

    All lists are aligned by index:

        image_paths[i]   -> path to image file for sample i
        label_vectors[i] -> list[int|None] of length == len(labels)
        label_masks[i]   -> list[int]       of length == len(labels)
    """

    image_paths: List[str]
    label_vectors: List[List[Optional[int]]]
    label_masks: List[List[int]]
    labels: List[Label]
    labelset: LabelSet
    frame_ids: List[int]
    video_ids: List[int]


def _new_frame_label_values() -> dict[int, dict[int, set[bool]]]:
    return defaultdict(lambda: defaultdict(set))


@dataclass(slots=True)
class _FrameLabelAccumulator:
    values_by_frame_label: dict[int, dict[int, set[bool]]] = field(
        default_factory=_new_frame_label_values
    )
    frame_by_id: dict[int, Frame] = field(default_factory=dict[int, Frame])
    frame_order: list[int] = field(default_factory=list[int])

    def add(
        self,
        frame: Frame,
        *,
        label_id: int,
        value: bool,
        label_index: dict[int, int],
    ) -> None:
        if label_id not in label_index:
            return
        frame_id = int(frame.pk)
        if frame_id not in self.frame_by_id:
            self.frame_by_id[frame_id] = frame
            self.frame_order.append(frame_id)
        self.values_by_frame_label[frame_id][label_id].add(value)


@dataclass(slots=True)
class _ImageMultilabelRows:
    image_paths: List[str] = field(default_factory=list[str])
    label_vectors: List[List[Optional[int]]] = field(
        default_factory=list[List[Optional[int]]]
    )
    label_masks: List[List[int]] = field(default_factory=list[List[int]])
    frame_ids: List[int] = field(default_factory=list[int])
    video_ids: List[int] = field(default_factory=list[int])


def _ensure_annotations_exist(
    dataset: AIDataSet,
    *,
    source_scope: AnnotationSourceScope,
    annotations_qs: models.QuerySet[ImageClassificationAnnotation],
    segments_qs: models.QuerySet[LabelVideoSegment],
) -> None:
    if not annotations_qs.exists() and not segments_qs.exists():
        raise ValueError(
            "AIDataSet id="
            f"{dataset.id} has no annotations attached for "
            f"annotation_source_scope={source_scope!r}."
        )


def _resolve_labelset(
    labelset: LabelSet | None,
    *,
    annotations_qs: models.QuerySet[ImageClassificationAnnotation],
    segments_qs: models.QuerySet[LabelVideoSegment],
) -> LabelSet:
    if labelset is not None:
        return labelset
    return infer_training_labelset(
        annotations_qs=annotations_qs,
        segments_qs=segments_qs,
    )


def _labels_and_index(labelset: LabelSet) -> tuple[List[Label], Dict[int, int]]:
    labels_in_order: List[Label] = labelset.get_labels_in_order()
    if not labels_in_order:
        labelset_id = int(getattr(labelset, "id"))
        raise ValueError(
            f"LabelSet id={labelset_id}, name='{labelset.name}' has no labels."
        )
    label_index: Dict[int, int] = {
        int(cast(Any, label).id): index
        for index, label in enumerate(labels_in_order)
        if cast(Any, label).id is not None
    }
    return labels_in_order, label_index


def _add_frame_annotations(
    accumulator: _FrameLabelAccumulator,
    *,
    annotations_qs: models.QuerySet[ImageClassificationAnnotation],
    label_index: dict[int, int],
) -> None:
    for annotation in annotations_qs.iterator():
        ann_any: Any = annotation
        accumulator.add(
            ann_any.frame,
            label_id=int(ann_any.label_id),
            value=bool(ann_any.value),
            label_index=label_index,
        )


def _add_segment_annotations(
    accumulator: _FrameLabelAccumulator,
    *,
    segments_qs: models.QuerySet[LabelVideoSegment],
    label_index: dict[int, int],
) -> None:
    frames_by_video_id_and_number = frames_for_training_segments(segments_qs)
    for segment in segments_qs.iterator():
        seg_any: Any = segment
        if seg_any.label_id not in label_index:
            continue
        frames_by_number = frames_by_video_id_and_number.get(
            int(seg_any.video_file_id), {}
        )
        for frame_number, frame in frames_by_number.items():
            if seg_any.start_frame_number <= frame_number < seg_any.end_frame_number:
                accumulator.add(
                    frame,
                    label_id=int(seg_any.label_id),
                    value=True,
                    label_index=label_index,
                )


def _sort_frame_order(accumulator: _FrameLabelAccumulator) -> None:
    accumulator.frame_order.sort(
        key=lambda frame_id: (
            int(getattr(accumulator.frame_by_id[frame_id], "video_id")),
            int(accumulator.frame_by_id[frame_id].frame_number),
            frame_id,
        )
    )


def _label_vector(
    accumulator: _FrameLabelAccumulator,
    *,
    frame_id: int,
    labels_in_order: List[Label],
    label_index: Dict[int, int],
) -> List[Optional[int]]:
    vector: List[Optional[int]] = [None] * len(labels_in_order)
    for label_id, values in accumulator.values_by_frame_label[frame_id].items():
        if len(values) > 1:
            label_name = labels_in_order[label_index[label_id]].name
            raise ValueError(
                "Conflicting AIDataSet training labels for "
                f"frame_id={frame_id} label={label_name!r}."
            )
        vector[label_index[label_id]] = 1 if next(iter(values)) else 0
    return vector


def _append_dataset_row(
    rows: _ImageMultilabelRows,
    *,
    frame: Frame,
    frame_id: int,
    vector: List[Optional[int]],
) -> None:
    file_path: Path = frame.file_path
    rows.image_paths.append(str(file_path))
    rows.label_vectors.append(vector)
    rows.label_masks.append([0 if value is None else 1 for value in vector])
    rows.frame_ids.append(frame_id)
    rows.video_ids.append(int(getattr(frame, "video_id")))


def _build_dataset_rows(
    accumulator: _FrameLabelAccumulator,
    *,
    labels_in_order: List[Label],
    label_index: Dict[int, int],
) -> _ImageMultilabelRows:
    rows = _ImageMultilabelRows()
    for frame_id in accumulator.frame_order:
        vector = _label_vector(
            accumulator,
            frame_id=frame_id,
            labels_in_order=labels_in_order,
            label_index=label_index,
        )
        _append_dataset_row(
            rows,
            frame=accumulator.frame_by_id[frame_id],
            frame_id=frame_id,
            vector=vector,
        )
    return rows


def build_image_multilabel_dataset_from_db(
    dataset: AIDataSet,
    labelset: Optional[LabelSet] = None,
    annotation_source_scope: str | None = ANNOTATION_SOURCE_SCOPE_ALL,
) -> ImageMultilabelDataset:
    """
    Build an image multi-label dataset from the AIDataSet selection.
    """
    if dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
        raise ValueError(
            f"build_image_multilabel_dataset_from_db expected dataset_type='image', "
            f"got '{dataset.dataset_type}' for AIDataSet id={dataset.id}."
        )

    source_scope = normalize_annotation_source_scope(annotation_source_scope)
    annotations_qs, segments_qs = training_annotation_querysets(
        dataset,
        source_scope=source_scope,
    )
    _ensure_annotations_exist(
        dataset,
        source_scope=source_scope,
        annotations_qs=annotations_qs,
        segments_qs=segments_qs,
    )
    resolved_labelset = _resolve_labelset(
        labelset,
        annotations_qs=annotations_qs,
        segments_qs=segments_qs,
    )
    labels_in_order, label_index = _labels_and_index(resolved_labelset)
    accumulator = _FrameLabelAccumulator()
    _add_frame_annotations(
        accumulator,
        annotations_qs=annotations_qs,
        label_index=label_index,
    )
    _add_segment_annotations(
        accumulator,
        segments_qs=segments_qs,
        label_index=label_index,
    )
    if not accumulator.frame_order:
        raise ValueError(
            "AIDataSet has no frame samples for the selected LabelSet. "
            "Ensure video annotations have initialized Frame rows."
        )
    _sort_frame_order(accumulator)
    rows = _build_dataset_rows(
        accumulator,
        labels_in_order=labels_in_order,
        label_index=label_index,
    )
    return ImageMultilabelDataset(
        image_paths=rows.image_paths,
        label_vectors=rows.label_vectors,
        label_masks=rows.label_masks,
        labels=labels_in_order,
        labelset=resolved_labelset,
        frame_ids=rows.frame_ids,
        video_ids=rows.video_ids,
    )


def build_dataset_for_training(
    dataset: AIDataSet,
    labelset: Optional[LabelSet] = None,
    annotation_source_scope: str | None = ANNOTATION_SOURCE_SCOPE_ALL,
):
    if (
        dataset.dataset_type == AIDataSet.DATASET_TYPE_IMAGE
        and dataset.ai_model_type == AIDataSet.AI_MODEL_TYPE_IMAGE_MULTILABEL
    ):
        return build_image_multilabel_dataset_from_db(
            dataset,
            labelset=labelset,
            annotation_source_scope=annotation_source_scope,
        )

    raise NotImplementedError(
        f"No dataset builder implemented for "
        f"dataset_type='{dataset.dataset_type}', "
        f"ai_model_type='{dataset.ai_model_type}'."
    )
