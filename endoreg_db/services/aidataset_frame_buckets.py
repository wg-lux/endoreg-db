from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Protocol, cast

from lx_dtypes.models.contracts.aidataset_frame_buckets import (
    AIDataSetFrameBucketCount,
    AIDataSetFrameBucketDistribution,
    AIDataSetFrameBucketSummary,
    AIDataSetLabelDistributionEntry,
    AIDataSetLabelFrameBucketCount,
    AIDataSetTargetFrameBucket,
)

if TYPE_CHECKING:
    from endoreg_db.models.aidataset.aidataset import AIDataSet
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_set import LabelSet
    from endoreg_db.models.label.label_video_segment.label_video_segment import (
        LabelVideoSegment,
    )


class _LabelRelationManager(Protocol):
    def filter(self, **kwargs: object) -> _LabelRelationManager: ...

    def exists(self) -> bool: ...


__all__ = [
    "AIDataSetFrameBucketCount",
    "AIDataSetFrameBucketDistribution",
    "AIDataSetFrameBucketSummary",
    "AIDataSetLabelDistributionEntry",
    "AIDataSetLabelFrameBucketCount",
    "AIDataSetTargetFrameBucket",
    "build_frame_bucket_distribution",
]


def _model_value(instance: object, field_name: str) -> object:
    return getattr(instance, field_name)


def _model_text(instance: object, field_name: str) -> str:
    return str(_model_value(instance, field_name) or "")


def _model_optional_text(instance: object, field_name: str) -> str | None:
    value = _model_value(instance, field_name)
    return str(value) if value not in {None, ""} else None


def _model_bool(instance: object, field_name: str) -> bool:
    value = _model_value(instance, field_name)
    if isinstance(value, bool):
        return value
    return bool(value)


def _model_int(instance: object, field_name: str) -> int:
    value = _model_value(instance, field_name)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError(f"{field_name} must be numeric.")


def _model_optional_int(instance: object, field_name: str) -> int | None:
    value = _model_value(instance, field_name)
    if value is None:
        return None
    return _model_int(instance, field_name)


def _model_datetime(instance: object, field_name: str) -> datetime:
    value = _model_value(instance, field_name)
    if isinstance(value, datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime.")


def _label_allowed_by_set(label_id: int | None, label_set: LabelSet | None) -> bool:
    if label_id is None:
        return False
    if label_set is None:
        return True
    labels = cast(_LabelRelationManager, _model_value(label_set, "labels"))
    return bool(labels.filter(pk=label_id).exists())


def _serialize_label_frame_buckets(
    buckets: dict[int, set[int]],
    *,
    label_names_by_id: dict[int, str],
) -> list[AIDataSetLabelFrameBucketCount]:
    return [
        AIDataSetLabelFrameBucketCount(
            label_id=label_id,
            label_name=label_names_by_id.get(label_id, f"Label {label_id}"),
            frame_count=len(frame_ids),
        )
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


def _union_frame_bucket_values(bucket_map: dict[int, set[int]]) -> set[int]:
    frame_ids: set[int] = set()
    for bucket_frame_ids in bucket_map.values():
        frame_ids.update(bucket_frame_ids)
    return frame_ids


def _build_target_frame_buckets(
    dataset: AIDataSet,
    *,
    target_label: Label | None,
) -> dict[AIDataSetTargetFrameBucket, set[int]]:
    if (
        _model_text(dataset, "dataset_type") != dataset.DATASET_TYPE_IMAGE
        or target_label is None
    ):
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
        frame_id = _model_int(annotation, "frame_id")
        seen_frame_ids.add(frame_id)
        if _model_optional_int(annotation, "label_id") == _model_int(
            target_label, "id"
        ):
            target_values_by_frame_id[frame_id].append(_model_bool(annotation, "value"))

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
) -> dict[int, AIDataSetLabelDistributionEntry]:
    distribution: dict[int, AIDataSetLabelDistributionEntry] = {}

    def ensure_label(label: Label | None) -> AIDataSetLabelDistributionEntry | None:
        if label is None or not _label_allowed_by_set(
            _model_optional_int(label, "pk"),
            label_set,
        ):
            return None
        label_id = _model_int(label, "pk")
        return distribution.setdefault(
            label_id,
            AIDataSetLabelDistributionEntry(
                label_id=label_id,
                label_name=_model_text(label, "name"),
            ),
        )

    for annotation in (
        dataset.image_annotations.select_related("label")
        .filter(label__isnull=False, frame__is_extracted=True)
        .iterator()
    ):
        entry = ensure_label(cast("Label", _model_value(annotation, "label")))
        if entry is None:
            continue
        if _model_bool(annotation, "value"):
            entry.frame_positive += 1
        else:
            entry.frame_negative += 1
        entry.total += 1

    for segment in (
        dataset.video_annotations.select_related("label")
        .filter(label__isnull=False)
        .iterator()
    ):
        entry = ensure_label(cast("Label", _model_value(segment, "label")))
        if entry is None:
            continue
        entry.segment_count += 1
        entry.total += 1

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
        label_id = _model_optional_int(annotation, "label_id")
        if not _label_allowed_by_set(label_id, label_set):
            continue
        if label_id is not None:
            buckets[label_id].add(_model_int(annotation, "frame_id"))

    return {label_id: frame_ids for label_id, frame_ids in buckets.items() if frame_ids}


def _build_segment_frame_buckets(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None,
    prediction_segments_only: bool,
) -> dict[int, set[int]]:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.state import frame_annotation as frame_annotation_state

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
    segments_by_video_id: dict[int, list["LabelVideoSegment"]] = defaultdict(list)
    is_prediction_segment_typed: Callable[["LabelVideoSegment"], bool] = cast(
        Callable[["LabelVideoSegment"], bool],
        getattr(frame_annotation_state, "is_prediction_segment"),
    )

    for segment in segments.iterator():
        if prediction_segments_only and not is_prediction_segment_typed(segment):
            continue
        label_id = _model_optional_int(segment, "label_id")
        if not _label_allowed_by_set(label_id, label_set):
            continue
        start_frame_number = _model_int(segment, "start_frame_number")
        end_frame_number = _model_int(segment, "end_frame_number")
        if start_frame_number >= end_frame_number:
            continue
        segments_by_video_id[_model_int(segment, "video_file_id")].append(segment)

    for video_id, video_segments in segments_by_video_id.items():
        min_start = min(
            _model_int(segment, "start_frame_number") for segment in video_segments
        )
        max_end = max(
            _model_int(segment, "end_frame_number") for segment in video_segments
        )
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
                    _model_int(segment, "start_frame_number")
                    <= frame_number
                    < _model_int(segment, "end_frame_number")
                ):
                    label_id = _model_optional_int(segment, "label_id")
                    if label_id is not None:
                        buckets[label_id].add(int(frame_id))

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
    from endoreg_db.models.label.label import Label

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
    label_names_by_id: dict[int, str] = {
        int(row["id"]): str(row["name"])
        for row in Label.objects.filter(id__in=label_ids).values("id", "name")
    }
    for label_id, entry in label_distribution.items():
        label_names_by_id.setdefault(label_id, entry.label_name)

    annotation_frame_ids = _union_frame_bucket_values(annotation_frame_buckets)
    segment_frame_ids = _union_frame_bucket_values(segment_frame_buckets)
    merged_frame_ids = _union_frame_bucket_values(merged_frame_buckets)

    return AIDataSetFrameBucketDistribution.model_validate(
        {
            "dataset_id": _model_int(dataset, "pk"),
            "name": _model_optional_text(dataset, "name"),
            "dataset_type": _model_text(dataset, "dataset_type"),
            "ai_model_type": _model_text(dataset, "ai_model_type"),
            "is_active": _model_bool(dataset, "is_active"),
            "updated_at": _model_datetime(dataset, "updated_at"),
            "label_group_id": (
                _model_optional_int(label_set, "pk") if label_set is not None else None
            ),
            "label_group_name": (
                _model_optional_text(label_set, "name")
                if label_set is not None
                else None
            ),
            "target_label_id": (
                _model_optional_int(target_label, "pk")
                if target_label is not None
                else None
            ),
            "target_label_name": (
                _model_optional_text(target_label, "name")
                if target_label is not None
                else None
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
                    -item.total,
                    item.label_name,
                    item.label_id,
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
