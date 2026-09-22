"""Request-local frame membership queries without per-row relation lookups."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from typing import TYPE_CHECKING, cast

from django.db.models import Count, Q, QuerySet

from endoreg_db.services.frame_annotation_segment_identity import is_prediction_segment

if TYPE_CHECKING:
    from endoreg_db.models.aidataset.aidataset import AIDataSet
    from endoreg_db.models.label.annotation.image_classification import (
        ImageClassificationAnnotation,
    )
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_set import LabelSet


def _annotations(
    dataset: AIDataSet, label_set: LabelSet | None, require_extracted_frames: bool
) -> QuerySet[ImageClassificationAnnotation]:
    rows = dataset.image_annotations.filter(frame__isnull=False)
    if label_set is not None:
        rows = rows.filter(label_id__in=label_set.labels.values("pk"))
    if require_extracted_frames:
        rows = rows.filter(frame__is_extracted=True)
    return rows


def build_dataset_target_buckets(
    *,
    dataset: AIDataSet | None,
    target_label: Label | None,
    require_extracted_frames: bool,
) -> dict[str, set[int]]:
    if dataset is None or dataset.dataset_type != "image" or target_label is None:
        return {}
    rows = (
        _annotations(dataset, None, require_extracted_frames)
        .order_by()
        .values("frame_id")
        .annotate(
            positive=Count("pk", filter=Q(label_id=target_label.pk, value=True)),
            negative=Count("pk", filter=Q(label_id=target_label.pk, value=False)),
        )
    )
    buckets: dict[str, set[int]] = defaultdict(set)
    for row in rows.iterator():
        bucket = (
            "positive"
            if row["positive"]
            else "negative"
            if row["negative"]
            else "unknown"
        )
        buckets[bucket].add(cast(int, row["frame_id"]))
    return dict(buckets)


def build_dataset_label_distribution(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
) -> dict[int, dict[str, int]]:
    if dataset is None:
        return {}
    distribution: dict[int, dict[str, int]] = {}

    def entry(label_id: int) -> dict[str, int]:
        return distribution.setdefault(
            label_id,
            {
                "label_id": label_id,
                "frame_positive": 0,
                "frame_negative": 0,
                "segment_count": 0,
                "total": 0,
            },
        )

    rows = (
        _annotations(dataset, label_set, True)
        .filter(label__isnull=False)
        .order_by()
        .values("label_id")
        .annotate(
            positive=Count("pk", filter=Q(value=True)),
            negative=Count("pk", filter=Q(value=False)),
        )
    )
    for row in rows:
        target = entry(cast(int, row["label_id"]))
        target["frame_positive"] = row["positive"]
        target["frame_negative"] = row["negative"]
        target["total"] = row["positive"] + row["negative"]
    segments = dataset.video_annotations.filter(label__isnull=False)
    if label_set is not None:
        segments = segments.filter(label_id__in=label_set.labels.values("pk"))
    for row in segments.order_by().values("label_id").annotate(count=Count("pk")):
        target = entry(cast(int, row["label_id"]))
        target["segment_count"] = row["count"]
        target["total"] += row["count"]
    return distribution


def build_segment_frame_buckets(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
    only_prediction_segments: bool,
    require_extracted_frames: bool,
) -> dict[int, set[int]]:
    if dataset is None:
        return {}
    from endoreg_db.models.media.frame.frame import Frame

    segments = dataset.video_annotations.select_related("source").filter(
        label__isnull=False,
        start_frame_number__isnull=False,
        end_frame_number__isnull=False,
    )
    if label_set is not None:
        segments = segments.filter(label_id__in=label_set.labels.values("pk"))
    # Merge overlapping intervals per video and label before visiting frame rows.
    intervals: dict[int, dict[int, list[tuple[int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for segment in segments.order_by(
        "video_file_id", "start_frame_number", "end_frame_number"
    ).iterator():
        if only_prediction_segments and not is_prediction_segment(segment):
            continue
        start, end = segment.start_frame_number, segment.end_frame_number
        if start >= end:
            continue
        video_id = cast(int, getattr(segment, "video_file_id"))
        label_id = cast(int, getattr(segment, "label_id"))
        label_intervals = intervals[video_id][label_id]
        if label_intervals and start <= label_intervals[-1][1]:
            previous_start, previous_end = label_intervals[-1]
            label_intervals[-1] = (previous_start, max(previous_end, end))
        else:
            label_intervals.append((start, end))

    buckets: dict[int, set[int]] = defaultdict(set)
    for video_id, label_intervals in intervals.items():
        bounds = [
            interval for ranges in label_intervals.values() for interval in ranges
        ]
        rows = Frame.objects.filter(
            video_id=video_id,
            frame_number__gte=min(start for start, _ in bounds),
            frame_number__lt=max(end for _, end in bounds),
        )
        if require_extracted_frames:
            rows = rows.filter(is_extracted=True)
        frame_rows = list(
            rows.order_by("frame_number").values_list("frame_number", "id")
        )
        numbers = [number for number, _ in frame_rows]
        for label_id, ranges in label_intervals.items():
            for start, end in ranges:
                left, right = bisect_left(numbers, start), bisect_left(numbers, end)
                buckets[label_id].update(
                    frame_id for _, frame_id in frame_rows[left:right]
                )
    return {label_id: ids for label_id, ids in buckets.items() if ids}


def build_annotation_frame_buckets(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
    require_extracted_frames: bool,
) -> dict[int, set[int]]:
    if dataset is None:
        return {}
    rows = (
        _annotations(dataset, label_set, require_extracted_frames)
        .filter(
            label__isnull=False,
            value=True,
        )
        .order_by()
        .values_list("label_id", "frame_id")
        .distinct()
    )
    buckets: dict[int, set[int]] = defaultdict(set)
    for label_id, frame_id in rows.iterator():
        buckets[label_id].add(frame_id)
    return dict(buckets)


def build_dataset_candidate_frame_ids(
    *,
    dataset: AIDataSet | None,
    label_set: LabelSet | None,
    only_prediction_segments: bool,
    require_extracted_frames: bool,
    segment_frame_buckets: dict[int, set[int]] | None = None,
) -> set[int] | None:
    if dataset is None:
        return None
    frame_ids = set(
        _annotations(dataset, label_set, require_extracted_frames)
        .filter(label__isnull=False)
        .order_by()
        .values_list("frame_id", flat=True)
        .distinct()
    )
    if segment_frame_buckets is None:
        segment_frame_buckets = build_segment_frame_buckets(
            dataset=dataset,
            label_set=label_set,
            only_prediction_segments=only_prediction_segments,
            require_extracted_frames=require_extracted_frames,
        )
    for ids in segment_frame_buckets.values():
        frame_ids.update(ids)
    return frame_ids
