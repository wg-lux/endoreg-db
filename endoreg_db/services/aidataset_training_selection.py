"""Shared, dataset-bounded selection of manual or confirmed training labels."""

from __future__ import annotations

from collections import defaultdict
from typing import Final, Literal, cast

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
from endoreg_db.services.frame_annotation_segment_identity import (
    MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES,
    manual_frame_annotation_preference_filter,
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
        return scope
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


def infer_training_labelset(
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
    labelsets_for_each_label: list[set[int]] = []
    for label in labels_qs:
        label_sets = cast(models.QuerySet[LabelSet], getattr(label, "label_sets"))
        labelset_ids = set(label_sets.values_list("id", flat=True))
        if not labelset_ids:
            raise ValueError(
                f"Label id={label.pk}, name='{label.name}' is not part of any LabelSet. "
                "Explicit LabelSet selection is required."
            )
        labelsets_for_each_label.append(labelset_ids)

    if not labelsets_for_each_label:
        raise ValueError(
            "No common LabelSet across all labels in this AIDataSet. "
            "Please specify a LabelSet explicitly."
        )

    common_ids: set[int] = labelsets_for_each_label[0].intersection(
        *labelsets_for_each_label[1:]
    )

    if not common_ids:
        raise ValueError(
            "No common LabelSet across all labels in this AIDataSet. "
            "Please specify a LabelSet explicitly."
        )
    if len(common_ids) > 1:
        raise ValueError(
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


def frames_for_training_segments(
    segments_qs: models.QuerySet[LabelVideoSegment],
) -> dict[int, dict[int, Frame]]:
    segments_by_video_id: dict[int, list[LabelVideoSegment]] = defaultdict(list)
    for segment in segments_qs.iterator():
        if segment.start_frame_number >= segment.end_frame_number:
            raise ValueError(
                f"Training segment id={segment.pk} has an invalid frame interval."
            )
        segments_by_video_id[int(getattr(segment, "video_file_id"))].append(segment)

    frames_by_video_id_and_number: dict[int, dict[int, Frame]] = {}
    for video_id, video_segments in segments_by_video_id.items():
        intervals = _merge_frame_intervals(
            [
                (
                    segment.start_frame_number,
                    segment.end_frame_number,
                )
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


def _manual_training_segments(dataset: AIDataSet) -> models.QuerySet[LabelVideoSegment]:
    # Exclude prediction types on the complete source relation, not on an
    # individual joined row: a source can have several information source types.
    return (
        dataset.video_annotations.filter(
            models.Q(source__name__in=MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES)
            | models.Q(
                source__information_source_types__name__in=[
                    "annotation",
                    "manual_annotation",
                ]
            )
        )
        .filter(prediction_meta_id__isnull=True)
        .exclude(source__information_source_types__name="prediction")
        .exclude(source__name__istartswith="prediction")
        .exclude(source__name__istartswith="model")
    )


def training_annotation_querysets(
    dataset: AIDataSet,
    *,
    source_scope: AnnotationSourceScope,
) -> tuple[
    models.QuerySet[ImageClassificationAnnotation],
    models.QuerySet[LabelVideoSegment],
]:
    annotations_qs = dataset.image_annotations.none()
    if uses_frame_annotations(source_scope):
        annotations_qs = (
            dataset.image_annotations.select_related("frame__video", "label")
            .filter(frame__isnull=False, label__isnull=False)
            .filter(manual_frame_annotation_preference_filter())
            .exclude(information_source__information_source_types__name="prediction")
            .exclude(information_source__name__istartswith="prediction")
            .exclude(information_source__name__istartswith="model")
            .distinct()
            .order_by("frame__video_id", "frame__frame_number", "label__name", "pk")
        )
    segments_qs = dataset.video_annotations.none()
    if uses_segment_annotations(source_scope):
        segments_qs = (
            dataset.video_annotations.select_related(
                "video_file", "label", "source", "state"
            )
            .filter(
                label__isnull=False,
                video_file_id__isnull=False,
                start_frame_number__isnull=False,
                end_frame_number__isnull=False,
            )
            .filter(
                models.Q(state__is_validated=True)
                | models.Q(pk__in=_manual_training_segments(dataset).values("pk"))
            )
            .distinct()
            .order_by(
                "video_file_id",
                "start_frame_number",
                "end_frame_number",
                "pk",
            )
        )
    return annotations_qs, segments_qs
