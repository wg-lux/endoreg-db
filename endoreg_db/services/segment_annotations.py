from __future__ import annotations

from typing import Sequence
from django.db.models import Q

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.models.state.frame_annotation import (
    segment_derived_external_annotation_id,
)


def ensure_segment_annotations(
    *,
    video_ids: Sequence[int] | None = None,
    segment_ids: Sequence[int] | None = None,
    information_source_name: str = "manual_annotation",
    commit: bool = True,
) -> dict[str, int]:
    """
    Ensure that the given segments have ImageClassificationAnnotations for each frame.

    Args:
        video_ids: Restrict processing to segments belonging to these videos.
        segment_ids: Restrict processing to specific segment primary keys.
        information_source_name: Name of the InformationSource row to annotate with.
        commit: If False, does not write anything but still computes how many annotations would be created.

    Returns:
        A summary dict with keys for processed segments, skipped reasons, and annotation counts.
    """
    if not video_ids and not segment_ids:
        raise ValueError("Either video_ids or segment_ids must be provided.")

    information_source, _ = InformationSource.objects.get_or_create(
        name=information_source_name,
        defaults={
            "description": "Automatic segment annotation generator",
        },
    )

    segments = LabelVideoSegment.objects.select_related("label")
    if segment_ids:
        segments = segments.filter(pk__in=segment_ids)
    else:
        segments = segments.filter(video_file_id__in=video_ids)

    summary = {
        "total_segments": segments.count(),
        "segments_processed": 0,
        "skipped_no_label": 0,
        "skipped_no_frames": 0,
        "annotations_needed": 0,
        "annotations_created": 0,
    }

    for segment in segments.order_by("pk"):
        label = segment.label
        if not label:
            summary["skipped_no_label"] += 1
            continue

        frame_ids = list(segment.get_frames().values_list("pk", flat=True))
        if not frame_ids:
            summary["skipped_no_frames"] += 1
            continue

        existing_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                frame_id__in=frame_ids,
                label=label,
                information_source=information_source,
            ).values_list("frame_id", flat=True)
        )

        missing_count = len(frame_ids) - len(existing_frame_ids)
        if missing_count <= 0:
            continue

        summary["segments_processed"] += 1
        summary["annotations_needed"] += missing_count

        if not commit:
            continue

        if segment.source_id != information_source.id:
            segment.source = information_source
            segment.save(update_fields=["source"])

        segment.generate_annotations()
        summary["annotations_created"] += missing_count

    return summary


def ensure_prediction_segment_annotations(
    *,
    video_ids: Sequence[int] | None = None,
    segment_ids: Sequence[int] | None = None,
    information_source_name: str = "prediction_annotation",
    commit: bool = True,
) -> dict[str, int]:
    """
    Ensure frame annotations exist for AI/prediction-based segments without touching
    manual annotations or rewriting the segment source.

    Eligible segments are those with either:
    - `prediction_meta` set (preferred marker), or
    - `source.name == "prediction"` (legacy/compat marker)
    """
    if not video_ids and not segment_ids:
        raise ValueError("Either video_ids or segment_ids must be provided.")

    information_source, _ = InformationSource.objects.get_or_create(
        name=information_source_name,
        defaults={
            "description": "Frame annotations derived from AI-generated segments",
        },
    )

    segments = LabelVideoSegment.objects.select_related(
        "label", "source", "prediction_meta", "prediction_meta__model_meta"
    )
    if segment_ids:
        segments = segments.filter(pk__in=segment_ids)
    else:
        segments = segments.filter(video_file_id__in=video_ids)

    segments = segments.filter(
        Q(prediction_meta__isnull=False) | Q(source__name="prediction")
    ).distinct()

    summary = {
        "total_segments": segments.count(),
        "eligible_prediction_segments": 0,
        "segments_processed": 0,
        "skipped_no_label": 0,
        "skipped_no_frames": 0,
        "annotations_needed": 0,
        "annotations_created": 0,
    }

    for segment in segments.order_by("pk"):
        summary["eligible_prediction_segments"] += 1
        label = segment.label
        if not label:
            summary["skipped_no_label"] += 1
            continue

        frame_ids = list(segment.get_frames().values_list("pk", flat=True))
        if not frame_ids:
            summary["skipped_no_frames"] += 1
            continue

        model_meta = None
        try:
            model_meta = segment.get_model_meta()
        except Exception:
            model_meta = None
        model_meta_id = model_meta.pk if model_meta else None

        existing_frame_ids = set(
            ImageClassificationAnnotation.objects.filter(
                frame_id__in=frame_ids,
                label=label,
                information_source=information_source,
                model_meta_id=model_meta_id,
            ).values_list("frame_id", flat=True)
        )

        missing_frame_ids = [fid for fid in frame_ids if fid not in existing_frame_ids]
        missing_count = len(missing_frame_ids)
        if missing_count <= 0:
            continue

        summary["segments_processed"] += 1
        summary["annotations_needed"] += missing_count

        if not commit:
            continue

        ImageClassificationAnnotation.objects.bulk_create(
            [
                ImageClassificationAnnotation(
                    frame_id=frame_id,
                    label=label,
                    value=True,
                    information_source=information_source,
                    model_meta_id=model_meta_id,
                    external_annotation_id=segment_derived_external_annotation_id(
                        segment_id=segment.pk,
                        frame_id=frame_id,
                        label_id=label.pk,
                        information_source_id=information_source.pk,
                        model_meta_id=model_meta_id,
                    ),
                )
                for frame_id in missing_frame_ids
            ],
            ignore_conflicts=True,
        )
        summary["annotations_created"] += missing_count

    return summary
