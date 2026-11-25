from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Protocol, cast

from lx_dtypes.models.contracts.aidataset_frame_buckets import (
    AIDataSetFrameBucketCount,
    AIDataSetFrameBucketDistribution,
    AIDataSetFrameBucketSummary,
    AIDataSetLabelDistributionEntry,
    AIDataSetLabelFrameBucketCount,
    AIDataSetTargetFrameBucket,
)

from endoreg_db.services.annotations.buckets import (
    build_annotation_frame_buckets,
    build_dataset_target_buckets,
    build_segment_frame_buckets,
)
from endoreg_db.utils.model_fields import (
    model_bool as _model_bool,
    model_datetime as _model_datetime,
    model_int as _model_int,
    model_optional_int as _model_optional_int,
    model_optional_text as _model_optional_text,
    model_text as _model_text,
    model_value as _model_value,
)

if TYPE_CHECKING:
    from endoreg_db.models.aidataset.aidataset import AIDataSet
    from endoreg_db.models.label.label import Label
    from endoreg_db.models.label.label_set import LabelSet


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
    buckets = build_dataset_target_buckets(
        dataset=dataset,
        target_label=target_label,
        require_extracted_frames=True,
    )
    if not buckets:
        return {}
    return {
        bucket: buckets.get(bucket.value, set())
        for bucket in AIDataSetTargetFrameBucket
    }


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
    return build_annotation_frame_buckets(
        dataset=dataset,
        label_set=label_set,
        require_extracted_frames=True,
    )


def _build_segment_frame_buckets(
    dataset: AIDataSet,
    *,
    label_set: LabelSet | None,
    prediction_segments_only: bool,
) -> dict[int, set[int]]:
    return build_segment_frame_buckets(
        dataset=dataset,
        label_set=label_set,
        only_prediction_segments=prediction_segments_only,
        require_extracted_frames=True,
    )


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
