from __future__ import annotations

from collections import defaultdict
from typing import Protocol, cast
from typing import Sequence
from django.db.models import Q, QuerySet

from endoreg_db.helpers.model_ids import model_fk
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.services.frame_annotation_segment_identity import (
    is_prediction_segment,
    manual_frame_annotation_preference_filter,
    segment_derived_external_annotation_id,
)


class _SegmentSourceLike(Protocol):
    source_id: int | None


class _InformationSourceLike(Protocol):
    pk: int


def _normalized_annotator(annotator: str | None) -> str | None:
    if annotator is None:
        return None
    normalized = str(annotator).strip()
    return normalized or None


def _model_meta_id(segment: LabelVideoSegment) -> int | None:
    try:
        model_meta = segment.get_model_meta()
    except Exception:
        return None
    if model_meta is None:
        return None
    return int(model_meta.pk)


def _segments_queryset(
    *,
    video_ids: Sequence[int] | None,
    segment_ids: Sequence[int] | None,
) -> QuerySet[LabelVideoSegment]:
    segments = LabelVideoSegment.objects.select_related(
        "label", "source", "prediction_meta", "prediction_meta__model_meta"
    )
    if segment_ids:
        return segments.filter(pk__in=segment_ids)
    if video_ids is None:
        raise ValueError("video_ids must be provided when segment_ids is absent")
    return segments.filter(video_file_id__in=video_ids)


def _frames_by_segment(
    segments: Sequence[LabelVideoSegment],
) -> dict[int, list[tuple[int, int]]]:
    if not segments:
        return {}

    video_ids = {model_fk(segment, "video_file") for segment in segments}
    min_frame = min(int(segment.start_frame_number) for segment in segments)
    max_frame = max(int(segment.end_frame_number) for segment in segments)
    candidate_frames = list(
        Frame.objects.filter(
            video_id__in=video_ids,
            frame_number__gte=min_frame,
            frame_number__lt=max_frame,
        )
        .only("id", "video_id", "frame_number")
        .order_by("video_id", "frame_number")
    )

    by_video: dict[int, list[Frame]] = defaultdict(list)
    for frame in candidate_frames:
        by_video[model_fk(frame, "video")].append(frame)

    result: dict[int, list[tuple[int, int]]] = {}
    for segment in segments:
        segment_pk = int(segment.pk)
        start_frame = int(segment.start_frame_number)
        end_frame = int(segment.end_frame_number)
        result[segment_pk] = [
            (int(frame.pk), int(frame.frame_number))
            for frame in by_video.get(model_fk(segment, "video_file"), [])
            if start_frame <= int(frame.frame_number) < end_frame
        ]
    return result


def _existing_annotation_keys(
    *,
    frame_ids: Sequence[int],
    annotator: str | None,
) -> set[tuple[int, int, int | None, int | None, str | None]]:
    if not frame_ids:
        return set()

    annotations = ImageClassificationAnnotation.objects.filter(frame_id__in=frame_ids)
    if annotator is not None:
        annotations = annotations.filter(annotator=annotator)

    annotation_rows = annotations.values_list(
        "frame_id",
        "label_id",
        "information_source_id",
        "model_meta_id",
        "annotator",
    )

    return {
        (
            int(frame_id),
            int(label_id),
            int(information_source_id) if information_source_id is not None else None,
            int(model_meta_id) if model_meta_id is not None else None,
            _normalized_annotator(cast(str | None, row_annotator)),
        )
        for (
            frame_id,
            label_id,
            information_source_id,
            model_meta_id,
            row_annotator,
        ) in annotation_rows
        if label_id is not None
    }


def _manual_preferred_frame_ids(
    *,
    frame_ids: Sequence[int],
    annotator: str | None,
) -> set[tuple[int, int, str | None]]:
    if not frame_ids:
        return set()

    preferred = ImageClassificationAnnotation.objects.filter(
        frame_id__in=frame_ids,
    ).filter(manual_frame_annotation_preference_filter())
    if annotator is None:
        preferred = preferred.filter(Q(annotator__isnull=True) | Q(annotator__exact=""))
    else:
        preferred = preferred.filter(annotator=annotator)

    return {
        (
            int(frame_id),
            int(label_id),
            _normalized_annotator(cast(str | None, row_annotator)),
        )
        for frame_id, label_id, row_annotator in preferred.values_list(
            "frame_id", "label_id", "annotator"
        )
        if label_id is not None
    }


def ensure_segment_annotations(
    *,
    video_ids: Sequence[int] | None = None,
    segment_ids: Sequence[int] | None = None,
    information_source_name: str = "manual_annotation",
    annotator: str | None = None,
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
    information_source_view = cast(_InformationSourceLike, information_source)

    normalized_annotator = _normalized_annotator(annotator)
    segments = _segments_queryset(video_ids=video_ids, segment_ids=segment_ids)
    segment_list = list(segments.order_by("pk"))
    frames_by_segment = _frames_by_segment(segment_list)
    all_frame_ids = [
        frame_id for frames in frames_by_segment.values() for frame_id, _ in frames
    ]
    existing_keys = _existing_annotation_keys(
        frame_ids=all_frame_ids,
        annotator=normalized_annotator,
    )
    manual_preferred_keys = _manual_preferred_frame_ids(
        frame_ids=all_frame_ids,
        annotator=normalized_annotator,
    )

    summary = {
        "total_segments": len(segment_list),
        "segments_processed": 0,
        "skipped_no_label": 0,
        "skipped_no_frames": 0,
        "annotations_needed": 0,
        "annotations_created": 0,
    }
    annotations_to_create: list[ImageClassificationAnnotation] = []
    source_updates: list[LabelVideoSegment] = []

    for segment in segment_list:
        segment_view = cast(_SegmentSourceLike, segment)
        label = segment.label
        if not label:
            summary["skipped_no_label"] += 1
            continue

        segment_pk = int(segment.pk)
        segment_frames = frames_by_segment.get(segment_pk, [])
        if not segment_frames:
            summary["skipped_no_frames"] += 1
            continue

        label_id = int(label.pk)
        information_source_id = int(information_source_view.pk)
        model_meta_id = _model_meta_id(segment)
        is_prediction = is_prediction_segment(segment)

        missing_frame_ids: list[int] = []
        for frame_id, _frame_number in segment_frames:
            existing_key = (
                frame_id,
                label_id,
                information_source_id,
                model_meta_id,
                normalized_annotator,
            )
            manual_key = (frame_id, label_id, normalized_annotator)
            if existing_key in existing_keys:
                continue
            if not is_prediction and manual_key in manual_preferred_keys:
                continue
            missing_frame_ids.append(frame_id)

        missing_count = len(missing_frame_ids)
        if missing_count <= 0:
            continue

        summary["segments_processed"] += 1
        summary["annotations_needed"] += missing_count

        if segment_view.source_id != information_source_view.pk:
            segment.source = information_source
            source_updates.append(segment)

        if commit:
            for frame_id in missing_frame_ids:
                annotation = ImageClassificationAnnotation(
                    frame_id=frame_id,
                    label_id=label_id,
                    value=True,
                    information_source_id=information_source_id,
                    model_meta_id=model_meta_id,
                    external_annotation_id=segment_derived_external_annotation_id(
                        segment_id=segment_pk,
                        frame_id=frame_id,
                        label_id=label_id,
                        information_source_id=information_source_id,
                        model_meta_id=model_meta_id,
                        annotator=normalized_annotator,
                    ),
                )
                if normalized_annotator is not None:
                    annotation.annotator = normalized_annotator
                annotations_to_create.append(annotation)

    if commit:
        if source_updates:
            LabelVideoSegment.objects.bulk_update(source_updates, ["source"])
        if annotations_to_create:
            ImageClassificationAnnotation.objects.bulk_create(
                annotations_to_create,
                ignore_conflicts=True,
            )
            summary["annotations_created"] = len(annotations_to_create)

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
    information_source_view = cast(_InformationSourceLike, information_source)

    segments = LabelVideoSegment.objects.select_related(
        "label", "source", "prediction_meta", "prediction_meta__model_meta"
    )
    if segment_ids:
        segments = segments.filter(pk__in=segment_ids)
    else:
        if video_ids is None:
            raise ValueError("video_ids must be provided when segment_ids is absent")
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
                        information_source_id=information_source_view.pk,
                        model_meta_id=model_meta_id,
                    ),
                )
                for frame_id in missing_frame_ids
            ],
            ignore_conflicts=True,
        )
        summary["annotations_created"] += missing_count

    return summary
