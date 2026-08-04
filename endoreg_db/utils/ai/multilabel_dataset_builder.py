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
from pathlib import Path
from typing import Dict, Final, List, Literal, Optional, TypedDict, cast

from django.db import models

from endoreg_db.models import (
    AIDataSet,
    Frame,
    ImageClassificationAnnotation,
    Label,
    LabelSet,
    LabelVideoSegment,
)

AnnotationSourceScope = Literal["all", "frame_only", "segment_only"]
ANNOTATION_SOURCE_SCOPE_ALL: Final[AnnotationSourceScope] = "all"
ANNOTATION_SOURCE_SCOPE_FRAME_ONLY: Final[AnnotationSourceScope] = "frame_only"
ANNOTATION_SOURCE_SCOPE_SEGMENT_ONLY: Final[AnnotationSourceScope] = "segment_only"

VALID_ANNOTATION_SOURCE_SCOPES: Final[frozenset[AnnotationSourceScope]] = frozenset(
    {
        ANNOTATION_SOURCE_SCOPE_ALL,
        ANNOTATION_SOURCE_SCOPE_FRAME_ONLY,
        ANNOTATION_SOURCE_SCOPE_SEGMENT_ONLY,
    }
)


def normalize_annotation_source_scope(
    value: str | None,
) -> AnnotationSourceScope:
    if value is None or value == "":
        return ANNOTATION_SOURCE_SCOPE_ALL
    scope = str(value).strip()
    if scope in VALID_ANNOTATION_SOURCE_SCOPES:
        return cast(AnnotationSourceScope, scope)
    raise ValueError(
        "annotation_source_scope must be one of: "
        f"{', '.join(sorted(VALID_ANNOTATION_SOURCE_SCOPES))}."
    )


def uses_frame_annotations(scope: AnnotationSourceScope) -> bool:
    return scope in {
        ANNOTATION_SOURCE_SCOPE_ALL,
        ANNOTATION_SOURCE_SCOPE_FRAME_ONLY,
    }


def uses_segment_annotations(scope: AnnotationSourceScope) -> bool:
    return scope in {
        ANNOTATION_SOURCE_SCOPE_ALL,
        ANNOTATION_SOURCE_SCOPE_SEGMENT_ONLY,
    }


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


def _label_ids_from_dataset(
    *,
    annotations_qs: models.QuerySet[ImageClassificationAnnotation],
    segments_qs: models.QuerySet[LabelVideoSegment],
) -> list[int]:
    label_ids = {
        int(label_id)
        for label_id in annotations_qs.values_list("label_id", flat=True).distinct()
        if label_id is not None
    }
    label_ids.update(
        int(label_id)
        for label_id in segments_qs.values_list("label_id", flat=True).distinct()
        if label_id is not None
    )
    return sorted(label_ids)


def _infer_labelset_from_dataset(
    *,
    annotations_qs: models.QuerySet[ImageClassificationAnnotation],
    segments_qs: models.QuerySet[LabelVideoSegment],
) -> LabelSet:
    label_ids = _label_ids_from_dataset(
        annotations_qs=annotations_qs,
        segments_qs=segments_qs,
    )
    if not label_ids:
        raise ValueError("Cannot infer LabelSet: AIDataSet has no labels.")

    labels_qs = Label.objects.filter(id__in=label_ids).prefetch_related("label_sets")
    labelsets_for_each_label = []

    for label in labels_qs:
        labelset_ids = list(label.label_sets.values_list("id", flat=True))
        if not labelset_ids:
            raise NotImplementedError(
                f"Label id={label.id}, name='{label.name}' is not part of any LabelSet. "
                "Explicit LabelSet selection is required."
            )
        labelsets_for_each_label.append(set(labelset_ids))

    common_ids = set.intersection(*labelsets_for_each_label)
    if not common_ids:
        raise NotImplementedError(
            "No common LabelSet across all labels in this AIDataSet. "
            "Please specify a LabelSet explicitly."
        )
    if len(common_ids) > 1:
        raise NotImplementedError(
            "More than one common LabelSet found for the labels in this AIDataSet. "
            "Please specify a LabelSet explicitly to disambiguate."
        )

    return LabelSet.objects.get(id=next(iter(common_ids)))


def _merge_frame_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]
    for start_frame, end_frame in intervals[1:]:
        previous_start, previous_end = merged[-1]
        if start_frame <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end_frame))
        else:
            merged.append((start_frame, end_frame))
    return merged


def _frame_interval_query(intervals: list[tuple[int, int]]) -> models.Q:
    frame_query = models.Q()
    for start_frame, end_frame in intervals:
        frame_query |= models.Q(
            frame_number__gte=start_frame,
            frame_number__lt=end_frame,
        )
    return frame_query


def _frames_for_video_segments(
    segments_qs: models.QuerySet[LabelVideoSegment],
) -> dict[int, dict[int, Frame]]:
    segments_by_video_id: dict[int, list[LabelVideoSegment]] = defaultdict(list)
    for segment in segments_qs.iterator():
        if segment.start_frame_number >= segment.end_frame_number:
            continue
        segments_by_video_id[segment.video_file_id].append(segment)

    frames_by_video_id_and_number: dict[int, dict[int, Frame]] = {}
    for video_id, video_segments in segments_by_video_id.items():
        intervals = _merge_frame_intervals(
            [
                (int(segment.start_frame_number), int(segment.end_frame_number))
                for segment in video_segments
            ]
        )
        if not intervals:
            continue

        frames_qs = Frame.objects.select_related("video").filter(video_id=video_id)
        if len(intervals) <= 120:
            frames_qs = frames_qs.filter(_frame_interval_query(intervals))
        else:
            frames_qs = frames_qs.filter(
                frame_number__gte=intervals[0][0],
                frame_number__lt=max(
                    end_frame for _start_frame, end_frame in intervals
                ),
            )

        frames_by_video_id_and_number[video_id] = {
            int(frame.frame_number): frame
            for frame in frames_qs.order_by("frame_number", "pk")
        }

    return frames_by_video_id_and_number


def build_image_multilabel_dataset_from_db(
    dataset: AIDataSet,
    labelset: Optional[LabelSet] = None,
    annotation_source_scope: str | None = ANNOTATION_SOURCE_SCOPE_ALL,
) -> ImageMultilabelDataset:
    """
    Build an image multi-label dataset from the AIDataSet selection.

    `AIDataSet.image_annotations` and `AIDataSet.video_annotations` are both
    canonical training inputs by default. `annotation_source_scope` can restrict
    the view to explicit frame annotations or segment-derived labels. Video
    segments are expanded in memory to the existing Frame rows inside their
    half-open frame ranges; this does not create ImageClassificationAnnotation
    rows.
    """
    if dataset.dataset_type != AIDataSet.DATASET_TYPE_IMAGE:
        raise ValueError(
            f"build_image_multilabel_dataset_from_db expected dataset_type='image', "
            f"got '{dataset.dataset_type}' for AIDataSet id={dataset.id}."
        )

    source_scope = normalize_annotation_source_scope(annotation_source_scope)

    annotations_qs = dataset.image_annotations.none()
    if uses_frame_annotations(source_scope):
        annotations_qs = (
            dataset.image_annotations.select_related("frame__video", "label")
            .filter(frame__isnull=False, label__isnull=False)
            .order_by("frame__video_id", "frame__frame_number", "label__name", "pk")
        )

    segments_qs = dataset.video_annotations.none()
    if uses_segment_annotations(source_scope):
        segments_qs = (
            dataset.video_annotations.select_related("video_file", "label")
            .filter(
                label__isnull=False,
                video_file_id__isnull=False,
                start_frame_number__isnull=False,
                end_frame_number__isnull=False,
            )
            .order_by(
                "video_file_id",
                "start_frame_number",
                "end_frame_number",
                "pk",
            )
        )

    if not annotations_qs.exists() and not segments_qs.exists():
        raise ValueError(
            "AIDataSet id="
            f"{dataset.id} has no annotations attached for "
            f"annotation_source_scope={source_scope!r}."
        )

    if labelset is None:
        labelset = _infer_labelset_from_dataset(
            annotations_qs=annotations_qs,
            segments_qs=segments_qs,
        )

    labels_in_order: List[Label] = labelset.get_labels_in_order()
    if not labels_in_order:
        raise ValueError(
            f"LabelSet id={labelset.id}, name='{labelset.name}' has no labels."
        )

    label_index: Dict[int, int] = {
        int(label.id): index
        for index, label in enumerate(labels_in_order)
        if label.id is not None
    }
    values_by_frame_label: dict[int, dict[int, set[bool]]] = defaultdict(
        lambda: defaultdict(set)
    )
    frame_by_id: dict[int, Frame] = {}
    frame_order: list[int] = []

    def add_frame_label(frame: Frame, label_id: int, value: bool) -> None:
        if label_id not in label_index:
            return
        frame_id = int(frame.pk)
        if frame_id not in frame_by_id:
            frame_by_id[frame_id] = frame
            frame_order.append(frame_id)
        values_by_frame_label[frame_id][int(label_id)].add(bool(value))

    for annotation in annotations_qs.iterator():
        add_frame_label(
            annotation.frame, int(annotation.label_id), bool(annotation.value)
        )

    frames_by_video_id_and_number = _frames_for_video_segments(segments_qs)
    for segment in segments_qs.iterator():
        if segment.label_id not in label_index:
            continue
        frames_by_number = frames_by_video_id_and_number.get(segment.video_file_id, {})
        for frame_number, frame in frames_by_number.items():
            if segment.start_frame_number <= frame_number < segment.end_frame_number:
                add_frame_label(frame, int(segment.label_id), True)

    if not frame_order:
        raise ValueError(
            "AIDataSet has no frame samples for the selected LabelSet. "
            "Ensure video annotations have initialized Frame rows."
        )

    frame_order.sort(
        key=lambda frame_id: (
            frame_by_id[frame_id].video_id,
            frame_by_id[frame_id].frame_number,
            frame_id,
        )
    )

    image_paths: List[str] = []
    label_vectors: List[List[Optional[int]]] = []
    label_masks: List[List[int]] = []
    frame_ids: List[int] = []
    video_ids: List[int] = []

    for frame_id in frame_order:
        frame = frame_by_id[frame_id]
        vector: List[Optional[int]] = [None] * len(labels_in_order)

        for label_id, values in values_by_frame_label[frame_id].items():
            if len(values) > 1:
                label_name = labels_in_order[label_index[label_id]].name
                raise ValueError(
                    "Conflicting AIDataSet training labels for "
                    f"frame_id={frame_id} label={label_name!r}."
                )
            vector[label_index[label_id]] = 1 if next(iter(values)) else 0

        mask: List[int] = [0 if value is None else 1 for value in vector]
        file_path: Path = frame.file_path
        image_paths.append(str(file_path))
        label_vectors.append(vector)
        label_masks.append(mask)
        frame_ids.append(frame_id)
        video_ids.append(frame.video_id)

    return ImageMultilabelDataset(
        image_paths=image_paths,
        label_vectors=label_vectors,
        label_masks=label_masks,
        labels=labels_in_order,
        labelset=labelset,
        frame_ids=frame_ids,
        video_ids=video_ids,
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
