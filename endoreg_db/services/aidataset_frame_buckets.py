from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from endoreg_db.models import AIDataSet, Label, LabelSet

__all__ = [
    "AIDataSetFrameBucketCount",
    "AIDataSetFrameBucketDistribution",
    "AIDataSetFrameBucketSummary",
    "AIDataSetLabelDistributionEntry",
    "AIDataSetLabelFrameBucketCount",
    "AIDataSetTargetFrameBucket",
    "build_frame_bucket_distribution",
]


class AIDataSetTargetFrameBucket(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


class AIDataSetFrameBucketCount(BaseModel):
    bucket: AIDataSetTargetFrameBucket
    frame_count: int = 0


class AIDataSetLabelDistributionEntry(BaseModel):
    label_id: int
    label_name: str
    frame_positive: int = 0
    frame_negative: int = 0
    segment_count: int = 0
    total: int = 0


class AIDataSetLabelFrameBucketCount(BaseModel):
    label_id: int
    label_name: str
    frame_count: int = 0


class AIDataSetFrameBucketSummary(BaseModel):
    image_annotation_count: int = 0
    video_annotation_count: int = 0
    annotation_frame_count: int = 0
    segment_frame_count: int = 0
    merged_frame_count: int = 0
    video_count: int = 0
    label_count: int = 0


class AIDataSetFrameBucketDistribution(BaseModel):
    schema_version: str = "1.0"
    dataset_id: int
    name: str | None = None
    dataset_type: str
    ai_model_type: str
    is_active: bool
    updated_at: datetime
    label_group_id: int | None = None
    label_group_name: str | None = None
    target_label_id: int | None = None
    target_label_name: str | None = None
    prediction_segments_only: bool = True
    summary: AIDataSetFrameBucketSummary
    target_buckets: list[AIDataSetFrameBucketCount] = Field(default_factory=list)
    label_distribution: list[AIDataSetLabelDistributionEntry] = Field(
        default_factory=list
    )
    annotation_frame_buckets: list[AIDataSetLabelFrameBucketCount] = Field(
        default_factory=list
    )
    segment_frame_buckets: list[AIDataSetLabelFrameBucketCount] = Field(
        default_factory=list
    )
    merged_frame_buckets: list[AIDataSetLabelFrameBucketCount] = Field(
        default_factory=list
    )


def _label_allowed_by_set(label_id: int | None, label_set: LabelSet | None) -> bool:
    if label_id is None:
        return False
    if label_set is None:
        return True
    return label_set.labels.filter(pk=label_id).exists()


def _serialize_label_frame_buckets(
    buckets: dict[int, set[int]],
    *,
    label_names_by_id: dict[int, str],
) -> list[dict[str, Any]]:
    return [
        {
            "label_id": label_id,
            "label_name": label_names_by_id.get(label_id, f"Label {label_id}"),
            "frame_count": len(frame_ids),
        }
        for label_id, frame_ids in sorted(
            buckets.items(),
            key=lambda item: (
                -len(item[1]),
                label_names_by_id.get(item[0], ""),
                item[0],
            ),
        )
        if frame_ids
    ]


def _merge_label_frame_buckets(
    *bucket_maps: dict[int, set[int]],
) -> dict[int, set[int]]:
    merged: dict[int, set[int]] = defaultdict(set)
    for bucket_map in bucket_maps:
        for label_id, frame_ids in bucket_map.items():
            merged[label_id].update(frame_ids)
    return {label_id: frame_ids for label_id, frame_ids in merged.items() if frame_ids}


def _build_target_frame_buckets(
    dataset: AIDataSet,
    *,
    target_label: Label | None,
) -> dict[AIDataSetTargetFrameBucket, set[int]]:
    if dataset.dataset_type != dataset.DATASET_TYPE_IMAGE or target_label is None:
        return {}

    annotations = dataset.image_annotations.select_related("frame", "label").filter(
        frame__isnull=False,
        frame__is_extracted=True,
    )
    if not annotations.exists():
        return {}

    frame_ids_by_bucket: dict[AIDataSetTargetFrameBucket, set[int]] = {
        AIDataSetTargetFrameBucket.POSITIVE: set(),
        AIDataSetTargetFrameBucket.NEGATIVE: set(),
        AIDataSetTargetFrameBucket.UNKNOWN: set(),
    }
    seen_frame_ids: set[int] = set()
    target_values_by_frame_id: dict[int, list[bool]] = defaultdict(list)

    for annotation in annotations.iterator():
        seen_frame_ids.add(annotation.frame_id)
        if annotation.label_id == target_label.id:
            target_values_by_frame_id[annotation.frame_id].append(
                bool(annotation.value)
            )

    for frame_id in seen_frame_ids:
        target_values = target_values_by_frame_id.get(frame_id, [])
        if any(target_values):
            frame_ids_by_bucket[AIDataSetTargetFrameBucket.POSITIVE].add(frame_id)
        elif target_values:
            frame_ids_by_bucket[AIDataSetTargetFrameBucket.NEGATIVE].add(frame_id)
        else:
            frame_ids_by_bucket[AIDataSetTargetFrameBucket.UNKNOWN].add(frame_id)

    return frame_ids_by_bucket


def _build_label_distribution(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None,
) -> dict[int, dict[str, Any]]:
    distribution: dict[int, dict[str, Any]] = {}

    def ensure_label(label: Label | None) -> dict[str, Any] | None:
        if label is None or not _label_allowed_by_set(label.pk, label_set):
            return None
        return distribution.setdefault(
            label.pk,
            {
                "label_id": label.pk,
                "label_name": label.name,
                "frame_positive": 0,
                "frame_negative": 0,
                "segment_count": 0,
                "total": 0,
            },
        )

    for annotation in (
        dataset.image_annotations.select_related("label")
        .filter(label__isnull=False, frame__is_extracted=True)
        .iterator()
    ):
        entry = ensure_label(annotation.label)
        if entry is None:
            continue
        if annotation.value:
            entry["frame_positive"] += 1
        else:
            entry["frame_negative"] += 1
        entry["total"] += 1

    for segment in (
        dataset.video_annotations.select_related("label")
        .filter(label__isnull=False)
        .iterator()
    ):
        entry = ensure_label(segment.label)
        if entry is None:
            continue
        entry["segment_count"] += 1
        entry["total"] += 1

    return distribution


def _build_annotation_frame_buckets(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None,
) -> dict[int, set[int]]:
    buckets: dict[int, set[int]] = defaultdict(set)
    annotations = dataset.image_annotations.select_related("label").filter(
        label__isnull=False,
        value=True,
        frame__isnull=False,
        frame__is_extracted=True,
    )

    for annotation in annotations.iterator():
        if not _label_allowed_by_set(annotation.label_id, label_set):
            continue
        buckets[annotation.label_id].add(annotation.frame_id)

    return {label_id: frame_ids for label_id, frame_ids in buckets.items() if frame_ids}


def _build_segment_frame_buckets(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None,
    prediction_segments_only: bool,
) -> dict[int, set[int]]:
    from endoreg_db.models import Frame
    from endoreg_db.models.state.frame_annotation import is_prediction_segment

    buckets: dict[int, set[int]] = defaultdict(set)
    segments = (
        dataset.video_annotations.select_related("label", "source")
        .filter(
            label__isnull=False,
            video_file_id__isnull=False,
            start_frame_number__isnull=False,
            end_frame_number__isnull=False,
        )
        .order_by("video_file_id", "start_frame_number", "end_frame_number")
    )
    segments_by_video_id: dict[int, list[Any]] = defaultdict(list)

    for segment in segments.iterator():
        if prediction_segments_only and not is_prediction_segment(segment):
            continue
        if not _label_allowed_by_set(segment.label_id, label_set):
            continue
        if segment.start_frame_number >= segment.end_frame_number:
            continue
        segments_by_video_id[segment.video_file_id].append(segment)

    for video_id, video_segments in segments_by_video_id.items():
        min_start = min(segment.start_frame_number for segment in video_segments)
        max_end = max(segment.end_frame_number for segment in video_segments)
        frame_rows = Frame.objects.filter(
            video_id=video_id,
            frame_number__gte=min_start,
            frame_number__lt=max_end,
            is_extracted=True,
        ).values_list("id", "frame_number")
        frame_ids_by_number = {
            frame_number: frame_id for frame_id, frame_number in frame_rows
        }

        for segment in video_segments:
            for frame_number, frame_id in frame_ids_by_number.items():
                if (
                    segment.start_frame_number
                    <= frame_number
                    < segment.end_frame_number
                ):
                    buckets[segment.label_id].add(frame_id)

    return {label_id: frame_ids for label_id, frame_ids in buckets.items() if frame_ids}


def build_frame_bucket_distribution(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None = None,
    target_label: Label | None = None,
    prediction_segments_only: bool = True,
) -> AIDataSetFrameBucketDistribution:
    """
    Return validated frame-bucket counts used by dataset-aware annotation flows.
    """
    from endoreg_db.models import Label

    target_buckets = _build_target_frame_buckets(
        dataset,
        target_label=target_label,
    )
    label_distribution = _build_label_distribution(dataset, label_set=label_set)
    annotation_frame_buckets = _build_annotation_frame_buckets(
        dataset,
        label_set=label_set,
    )
    segment_frame_buckets = _build_segment_frame_buckets(
        dataset,
        label_set=label_set,
        prediction_segments_only=prediction_segments_only,
    )
    merged_frame_buckets = _merge_label_frame_buckets(
        annotation_frame_buckets,
        segment_frame_buckets,
    )

    label_ids = set(label_distribution)
    label_ids.update(annotation_frame_buckets)
    label_ids.update(segment_frame_buckets)
    label_ids.update(merged_frame_buckets)
    label_names_by_id = {
        row["id"]: row["name"]
        for row in Label.objects.filter(id__in=label_ids).values("id", "name")
    }
    for label_id, entry in label_distribution.items():
        label_names_by_id.setdefault(label_id, entry["label_name"])

    annotation_frame_ids = (
        set().union(*annotation_frame_buckets.values())
        if annotation_frame_buckets
        else set()
    )
    segment_frame_ids = (
        set().union(*segment_frame_buckets.values()) if segment_frame_buckets else set()
    )
    merged_frame_ids = (
        set().union(*merged_frame_buckets.values()) if merged_frame_buckets else set()
    )

    return AIDataSetFrameBucketDistribution.model_validate(
        {
            "dataset_id": dataset.pk,
            "name": dataset.name,
            "dataset_type": dataset.dataset_type,
            "ai_model_type": dataset.ai_model_type,
            "is_active": dataset.is_active,
            "updated_at": dataset.updated_at,
            "label_group_id": label_set.pk if label_set is not None else None,
            "label_group_name": label_set.name if label_set is not None else None,
            "target_label_id": target_label.pk if target_label is not None else None,
            "target_label_name": (
                target_label.name if target_label is not None else None
            ),
            "prediction_segments_only": prediction_segments_only,
            "summary": {
                "image_annotation_count": dataset.image_annotations.count(),
                "video_annotation_count": dataset.video_annotations.count(),
                "annotation_frame_count": len(annotation_frame_ids),
                "segment_frame_count": len(segment_frame_ids),
                "merged_frame_count": len(merged_frame_ids),
                "video_count": dataset.get_related_videos_queryset().count(),
                "label_count": len(label_ids),
            },
            "target_buckets": [
                {
                    "bucket": bucket,
                    "frame_count": len(target_buckets.get(bucket, set())),
                }
                for bucket in AIDataSetTargetFrameBucket
            ],
            "label_distribution": sorted(
                label_distribution.values(),
                key=lambda item: (
                    -item["total"],
                    item["label_name"],
                    item["label_id"],
                ),
            ),
            "annotation_frame_buckets": _serialize_label_frame_buckets(
                annotation_frame_buckets,
                label_names_by_id=label_names_by_id,
            ),
            "segment_frame_buckets": _serialize_label_frame_buckets(
                segment_frame_buckets,
                label_names_by_id=label_names_by_id,
            ),
            "merged_frame_buckets": _serialize_label_frame_buckets(
                merged_frame_buckets,
                label_names_by_id=label_names_by_id,
            ),
        }
    )
