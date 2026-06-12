from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections.abc import Iterable
from typing import Literal, Protocol, cast

from django.db import models, transaction
from django.db.models import Q

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.other.information_source import InformationSource
from endoreg_db.models.state.frame_annotation import (
    MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES,
    PREDICTION_INFORMATION_SOURCE_NAMES,
    SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX,
    is_prediction_segment,
    is_segment_derived_external_annotation_id,
    manual_annotation_filter,
    non_segment_derived_annotation_filter,
    prediction_annotation_filter,
    segment_derived_external_annotation_id,
    segment_derived_external_annotation_prefix_for_segment,
)

FrameSegmentTrack = Literal["all", "manual", "prediction"]
VALID_FRAME_SEGMENT_TRACKS: set[str] = {"all", "manual", "prediction"}


class _ModelMetaLike(Protocol):
    pk: int


class _FrameLike(Protocol):
    pk: int
    frame_number: int
    video_id: int


class _AnnotationInformationSourceTypeQuerySetLike(Protocol):
    def filter(
        self, *args: object, **kwargs: object
    ) -> "_AnnotationInformationSourceTypeQuerySetLike": ...

    def exists(self) -> bool: ...


class _AnnotationInformationSourceLike(Protocol):
    name: str
    information_source_types: _AnnotationInformationSourceTypeQuerySetLike


class _LabelLike(Protocol):
    pk: int


class _SegmentLike(Protocol):
    pk: int
    label_id: int | None
    source_id: int | None
    video_file_id: int

    def get_model_meta(self) -> _ModelMetaLike | None: ...

    def get_frames(self) -> "_FrameQuerySetLike": ...


class _FrameQuerySetLike(Protocol):
    def only(self, *fields: str) -> Iterable[_FrameLike]: ...


class _AnnotationLike(Protocol):
    pk: int
    frame: _FrameLike
    frame_id: int
    label: _LabelLike
    label_id: int
    information_source: _AnnotationInformationSourceLike | None
    information_source_id: int | None
    model_meta_id: int | None
    annotator: str | None
    external_annotation_id: str | None


def _frame_segment_reconciliation_issues() -> list[FrameSegmentReconciliationIssue]:
    return []


@dataclass(frozen=True)
class FrameSegmentReconciliationSpec:
    video_ids: tuple[int, ...] = ()
    segment_ids: tuple[int, ...] = ()
    annotator: str | None = None
    track: FrameSegmentTrack = "all"
    apply: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "video_ids": list(self.video_ids),
            "segment_ids": list(self.segment_ids),
            "annotator": _normalize_annotator(self.annotator),
            "track": self.track,
            "apply": self.apply,
            "dry_run": not self.apply,
        }


@dataclass(frozen=True)
class AnnotationMatchKey:
    track: str
    frame_id: int
    label_id: int
    information_source_id: int | None
    model_meta_id: int | None
    annotator: str


@dataclass(frozen=True)
class FrameSegmentReconciliationIssue:
    issue_type: str
    track: str
    video_id: int | None = None
    segment_id: int | None = None
    annotation_id: int | None = None
    frame_id: int | None = None
    frame_number: int | None = None
    label_id: int | None = None
    information_source_id: int | None = None
    model_meta_id: int | None = None
    annotator: str = ""
    external_annotation_id: str | None = None
    action: str = "report_only"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class FrameSegmentReconciliationSummary:
    total_segments: int = 0
    eligible_segments: int = 0
    skipped_no_label: int = 0
    skipped_no_frames: int = 0
    skipped_no_source: int = 0
    expected_annotations: int = 0
    matched_annotations: int = 0
    legacy_matched_annotations: int = 0
    missing_annotations: int = 0
    created_annotations: int = 0
    generated_matched_annotations: int = 0
    stale_generated_annotations: int = 0
    deleted_stale_generated_annotations: int = 0
    suspicious_unmarked_annotations: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class FrameSegmentReconciliationReport:
    spec: FrameSegmentReconciliationSpec
    summary: FrameSegmentReconciliationSummary
    issues: list[FrameSegmentReconciliationIssue] = field(
        default_factory=_frame_segment_reconciliation_issues
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "spec": self.spec.as_dict(),
            "summary": self.summary.as_dict(),
            "issues": [issue.as_dict() for issue in self.issues],
        }


def _normalize_annotator(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _annotator_key(value: str | None) -> str:
    return _normalize_annotator(value) or ""


def _annotator_scope_q(annotator: str | None) -> Q:
    normalized = _normalize_annotator(annotator)
    if normalized is None:
        return Q(annotator__isnull=True) | Q(annotator__exact="")
    return Q(annotator=normalized)


def _track_allowed(requested_track: str, actual_track: str) -> bool:
    return requested_track == "all" or requested_track == actual_track


def _segment_track(segment: LabelVideoSegment) -> str:
    return "prediction" if is_prediction_segment(segment) else "manual"


def _segment_model_meta_id(segment: LabelVideoSegment) -> int | None:
    segment_view = cast(_SegmentLike, segment)
    try:
        model_meta = segment_view.get_model_meta()
    except Exception:
        return None
    return model_meta.pk if model_meta else None


def _annotation_source_name(annotation: ImageClassificationAnnotation) -> str:
    annotation_view = cast(_AnnotationLike, annotation)
    source = annotation_view.information_source
    return (source.name if source is not None else "").strip().lower()


def _annotation_has_source_type(
    annotation: ImageClassificationAnnotation,
    source_type_name: str,
) -> bool:
    annotation_view = cast(_AnnotationLike, annotation)
    source = annotation_view.information_source
    if source is None:
        return False
    return source.information_source_types.filter(name=source_type_name).exists()


def _annotation_track(annotation: ImageClassificationAnnotation) -> str:
    annotation_view = cast(_AnnotationLike, annotation)
    source_name = _annotation_source_name(annotation)
    if (
        annotation_view.model_meta_id is not None
        or source_name in PREDICTION_INFORMATION_SOURCE_NAMES
        or source_name.startswith("prediction")
        or source_name.startswith("model")
        or _annotation_has_source_type(annotation, "prediction")
    ):
        return "prediction"
    if (
        source_name in MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES
        or _annotation_has_source_type(annotation, "annotation")
        or _annotation_has_source_type(annotation, "manual_annotation")
    ):
        return "manual"
    return "unknown"


def _expected_key(
    *,
    track: str,
    frame_id: int,
    label_id: int,
    information_source_id: int | None,
    model_meta_id: int | None,
    annotator: str | None,
) -> AnnotationMatchKey:
    return AnnotationMatchKey(
        track=track,
        frame_id=frame_id,
        label_id=label_id,
        information_source_id=(
            None if track in {"manual", "prediction"} else information_source_id
        ),
        model_meta_id=model_meta_id,
        annotator=_annotator_key(annotator),
    )


def _annotation_key(
    annotation: ImageClassificationAnnotation,
    *,
    track: str | None = None,
) -> AnnotationMatchKey:
    annotation_view = cast(_AnnotationLike, annotation)
    resolved_track = track or _annotation_track(annotation)
    return AnnotationMatchKey(
        track=resolved_track,
        frame_id=annotation_view.frame_id,
        label_id=annotation_view.label_id,
        information_source_id=(
            None
            if resolved_track in {"manual", "prediction"}
            else annotation_view.information_source_id
        ),
        model_meta_id=annotation_view.model_meta_id,
        annotator=_annotator_key(annotation_view.annotator),
    )


def _selected_segments(spec: FrameSegmentReconciliationSpec) -> list[LabelVideoSegment]:
    queryset = LabelVideoSegment.objects.select_related(
        "video_file",
        "label",
        "source",
        "prediction_meta",
        "prediction_meta__model_meta",
    ).order_by("pk")
    if spec.video_ids:
        queryset = queryset.filter(video_file_id__in=spec.video_ids)
    if spec.segment_ids:
        queryset = queryset.filter(pk__in=spec.segment_ids)
    return list(queryset)


def _prediction_annotation_source(*, create: bool) -> InformationSource | None:
    queryset = InformationSource.objects.filter(name="prediction_annotation").order_by(
        "id"
    )
    source = queryset.first()
    if source is not None or not create:
        return source
    source, _ = InformationSource.objects.get_or_create(
        name="prediction_annotation",
        defaults={
            "description": "Frame annotations derived from AI-generated segments",
        },
    )
    return source


def _existing_annotations_for_segment(
    *,
    segment: LabelVideoSegment,
    frame_ids: list[int],
    track: str,
    model_meta_id: int | None,
    annotator: str | None,
) -> list[ImageClassificationAnnotation]:
    segment_view = cast(_SegmentLike, segment)
    queryset = ImageClassificationAnnotation.objects.select_related(
        "information_source", "frame", "label"
    ).filter(
        frame_id__in=frame_ids,
    )
    if segment_view.label_id is None:
        return []
    queryset = queryset.filter(label_id=segment_view.label_id)
    if model_meta_id is None:
        queryset = queryset.filter(model_meta__isnull=True)
    else:
        queryset = queryset.filter(model_meta_id=model_meta_id)
    queryset = queryset.filter(_annotator_scope_q(annotator))
    if track == "prediction":
        queryset = queryset.filter(prediction_annotation_filter())
    else:
        queryset = queryset.filter(manual_annotation_filter())
    return list(queryset.order_by("pk").distinct())


def _generated_annotation_queryset(
    *,
    spec: FrameSegmentReconciliationSpec,
    scope_video_ids: set[int],
) -> models.QuerySet[ImageClassificationAnnotation]:
    queryset = ImageClassificationAnnotation.objects.select_related(
        "frame",
        "label",
        "information_source",
    ).filter(
        external_annotation_id__startswith=(
            f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
        )
    )
    if scope_video_ids:
        queryset = queryset.filter(frame__video_id__in=scope_video_ids)
    if spec.segment_ids:
        segment_q = Q()
        for segment_id in spec.segment_ids:
            segment_q |= Q(
                external_annotation_id__startswith=(
                    segment_derived_external_annotation_prefix_for_segment(segment_id)
                )
            )
        queryset = queryset.filter(segment_q)
    if spec.track == "manual":
        queryset = queryset.filter(manual_annotation_filter())
    elif spec.track == "prediction":
        queryset = queryset.filter(prediction_annotation_filter())
    return queryset.filter(_annotator_scope_q(spec.annotator)).order_by("pk")


def _unmarked_annotation_queryset(
    *,
    spec: FrameSegmentReconciliationSpec,
    scope_video_ids: set[int],
    scope_frame_ids: set[int],
) -> models.QuerySet[ImageClassificationAnnotation]:
    queryset = ImageClassificationAnnotation.objects.select_related(
        "frame",
        "label",
        "information_source",
    ).filter(non_segment_derived_annotation_filter())
    if spec.segment_ids:
        if not scope_frame_ids:
            return queryset.none()
        queryset = queryset.filter(frame_id__in=scope_frame_ids)
    elif scope_video_ids:
        queryset = queryset.filter(frame__video_id__in=scope_video_ids)
    if spec.track == "manual":
        queryset = queryset.filter(manual_annotation_filter())
    elif spec.track == "prediction":
        queryset = queryset.filter(prediction_annotation_filter())
    return queryset.filter(_annotator_scope_q(spec.annotator)).order_by("pk")


def reconcile_frame_segment_annotations(
    spec: FrameSegmentReconciliationSpec,
) -> FrameSegmentReconciliationReport:
    if spec.track not in VALID_FRAME_SEGMENT_TRACKS:
        raise ValueError("track must be one of: all, manual, prediction.")

    summary = FrameSegmentReconciliationSummary()
    issues: list[FrameSegmentReconciliationIssue] = []
    annotations_to_create: list[ImageClassificationAnnotation] = []
    stale_annotation_ids: list[int] = []
    expected_keys: set[AnnotationMatchKey] = set()
    scope_frame_ids: set[int] = set()
    scope_video_ids: set[int] = set(spec.video_ids)

    segments = _selected_segments(spec)
    summary.total_segments = len(segments)

    for segment in segments:
        segment_view = cast(_SegmentLike, segment)
        scope_video_ids.add(segment_view.video_file_id)
        track = _segment_track(segment)
        if not _track_allowed(spec.track, track):
            continue

        summary.eligible_segments += 1
        if segment_view.label_id is None:
            summary.skipped_no_label += 1
            issues.append(
                FrameSegmentReconciliationIssue(
                    issue_type="skipped_no_label",
                    track=track,
                    video_id=segment_view.video_file_id,
                    segment_id=segment_view.pk,
                )
            )
            continue

        frames = list(segment.get_frames().only("id", "frame_number"))
        scope_frame_ids.update(frame.pk for frame in frames)
        if not frames:
            summary.skipped_no_frames += 1
            issues.append(
                FrameSegmentReconciliationIssue(
                    issue_type="skipped_no_frames",
                    track=track,
                    video_id=segment_view.video_file_id,
                    segment_id=segment_view.pk,
                    label_id=segment_view.label_id,
                )
            )
            continue

        model_meta_id = _segment_model_meta_id(segment)
        create_source_id = segment_view.source_id
        if track == "prediction":
            prediction_source = _prediction_annotation_source(create=spec.apply)
            create_source_id = prediction_source.pk if prediction_source else None
        elif create_source_id is None:
            summary.skipped_no_source += 1
            issues.append(
                FrameSegmentReconciliationIssue(
                    issue_type="skipped_no_source",
                    track=track,
                    video_id=segment_view.video_file_id,
                    segment_id=segment_view.pk,
                    label_id=segment_view.label_id,
                )
            )
            continue

        frame_ids = [frame.pk for frame in frames]
        existing_annotations = _existing_annotations_for_segment(
            segment=segment,
            frame_ids=frame_ids,
            track=track,
            model_meta_id=model_meta_id,
            annotator=spec.annotator,
        )
        existing_by_key: dict[
            AnnotationMatchKey, list[ImageClassificationAnnotation]
        ] = {}
        for annotation in existing_annotations:
            key = _annotation_key(annotation, track=track)
            existing_by_key.setdefault(key, []).append(annotation)

        for frame in frames:
            summary.expected_annotations += 1
            expected_key = _expected_key(
                track=track,
                frame_id=frame.pk,
                label_id=segment_view.label_id,
                information_source_id=segment_view.source_id,
                model_meta_id=model_meta_id,
                annotator=spec.annotator,
            )
            expected_keys.add(expected_key)
            matching_annotations = existing_by_key.get(expected_key, [])
            if matching_annotations:
                summary.matched_annotations += 1
                if any(
                    not is_segment_derived_external_annotation_id(
                        annotation.external_annotation_id
                    )
                    for annotation in matching_annotations
                ):
                    summary.legacy_matched_annotations += 1
                    matching_annotation = cast(_AnnotationLike, matching_annotations[0])
                    issues.append(
                        FrameSegmentReconciliationIssue(
                            issue_type="legacy_matched_annotation",
                            track=track,
                            video_id=segment_view.video_file_id,
                            segment_id=segment_view.pk,
                            annotation_id=matching_annotation.pk,
                            frame_id=frame.pk,
                            frame_number=frame.frame_number,
                            label_id=segment_view.label_id,
                            information_source_id=(
                                matching_annotation.information_source_id
                            ),
                            model_meta_id=model_meta_id,
                            annotator=_annotator_key(matching_annotation.annotator),
                            external_annotation_id=(
                                matching_annotation.external_annotation_id
                            ),
                            action="report_only",
                        )
                    )
                continue

            summary.missing_annotations += 1
            action = (
                "create"
                if spec.apply and create_source_id is not None
                else "report_only"
            )
            issues.append(
                FrameSegmentReconciliationIssue(
                    issue_type="missing_annotation",
                    track=track,
                    video_id=segment_view.video_file_id,
                    segment_id=segment_view.pk,
                    frame_id=frame.pk,
                    frame_number=frame.frame_number,
                    label_id=segment_view.label_id,
                    information_source_id=create_source_id,
                    model_meta_id=model_meta_id,
                    annotator=_annotator_key(spec.annotator),
                    action=action,
                )
            )
            if spec.apply and create_source_id is not None:
                annotations_to_create.append(
                    ImageClassificationAnnotation(
                        frame_id=frame.pk,
                        label_id=segment_view.label_id,
                        value=True,
                        information_source_id=create_source_id,
                        model_meta_id=model_meta_id,
                        annotator=_normalize_annotator(spec.annotator),
                        external_annotation_id=(
                            segment_derived_external_annotation_id(
                                segment_id=segment_view.pk,
                                frame_id=frame.pk,
                                label_id=segment_view.label_id,
                                information_source_id=create_source_id,
                                model_meta_id=model_meta_id,
                                annotator=_normalize_annotator(spec.annotator),
                            )
                        ),
                    )
                )

    for annotation in _generated_annotation_queryset(
        spec=spec,
        scope_video_ids=scope_video_ids,
    ).iterator():
        annotation_view = cast(_AnnotationLike, annotation)
        track = _annotation_track(annotation)
        if not _track_allowed(spec.track, track):
            continue
        key = _annotation_key(annotation, track=track)
        if key in expected_keys:
            summary.generated_matched_annotations += 1
            continue
        summary.stale_generated_annotations += 1
        stale_annotation_ids.append(annotation.pk)
        issues.append(
            FrameSegmentReconciliationIssue(
                issue_type="stale_generated_annotation",
                track=track,
                video_id=annotation_view.frame.video_id,
                annotation_id=annotation_view.pk,
                frame_id=annotation_view.frame_id,
                frame_number=annotation_view.frame.frame_number,
                label_id=annotation_view.label_id,
                information_source_id=annotation_view.information_source_id,
                model_meta_id=annotation_view.model_meta_id,
                annotator=_annotator_key(annotation_view.annotator),
                external_annotation_id=annotation_view.external_annotation_id,
                action="delete" if spec.apply else "report_only",
            )
        )

    for annotation in _unmarked_annotation_queryset(
        spec=spec,
        scope_video_ids=scope_video_ids,
        scope_frame_ids=scope_frame_ids,
    ).iterator():
        annotation_view = cast(_AnnotationLike, annotation)
        track = _annotation_track(annotation)
        if not _track_allowed(spec.track, track):
            continue
        key = _annotation_key(annotation, track=track)
        if key in expected_keys:
            continue
        summary.suspicious_unmarked_annotations += 1
        issues.append(
            FrameSegmentReconciliationIssue(
                issue_type="suspicious_unmarked_annotation",
                track=track,
                video_id=annotation_view.frame.video_id,
                annotation_id=annotation_view.pk,
                frame_id=annotation_view.frame_id,
                frame_number=annotation_view.frame.frame_number,
                label_id=annotation_view.label_id,
                information_source_id=annotation_view.information_source_id,
                model_meta_id=annotation_view.model_meta_id,
                annotator=_annotator_key(annotation_view.annotator),
                external_annotation_id=annotation_view.external_annotation_id,
                action="report_only",
            )
        )

    if spec.apply and (annotations_to_create or stale_annotation_ids):
        with transaction.atomic():
            if annotations_to_create:
                ImageClassificationAnnotation.objects.bulk_create(
                    annotations_to_create,
                    ignore_conflicts=True,
                )
                summary.created_annotations = len(annotations_to_create)
            if stale_annotation_ids:
                deleted, _ = ImageClassificationAnnotation.objects.filter(
                    pk__in=stale_annotation_ids,
                    external_annotation_id__startswith=(
                        f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
                    ),
                ).delete()
                summary.deleted_stale_generated_annotations = deleted

    return FrameSegmentReconciliationReport(
        spec=spec,
        summary=summary,
        issues=issues,
    )
