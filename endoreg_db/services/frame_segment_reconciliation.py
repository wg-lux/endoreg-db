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


def _annotation_create_list() -> list[ImageClassificationAnnotation]:
    return []


def _integer_list() -> list[int]:
    return []


def _annotation_key_set() -> set[AnnotationMatchKey]:
    return set()


def _integer_set() -> set[int]:
    return set()


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


@dataclass
class _ReconciliationContext:
    spec: FrameSegmentReconciliationSpec
    summary: FrameSegmentReconciliationSummary = field(
        default_factory=FrameSegmentReconciliationSummary
    )
    issues: list[FrameSegmentReconciliationIssue] = field(
        default_factory=_frame_segment_reconciliation_issues
    )
    annotations_to_create: list[ImageClassificationAnnotation] = field(
        default_factory=_annotation_create_list
    )
    stale_annotation_ids: list[int] = field(default_factory=_integer_list)
    expected_keys: set[AnnotationMatchKey] = field(default_factory=_annotation_key_set)
    scope_frame_ids: set[int] = field(default_factory=_integer_set)
    scope_video_ids: set[int] = field(default_factory=_integer_set)


@dataclass(frozen=True)
class _EligibleSegment:
    segment: LabelVideoSegment
    segment_view: _SegmentLike
    track: str
    frames: list[_FrameLike]
    model_meta_id: int | None
    create_source_id: int


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


def _segment_frames_or_issue(
    context: _ReconciliationContext,
    segment: LabelVideoSegment,
    segment_view: _SegmentLike,
    track: str,
) -> list[_FrameLike] | None:
    if segment_view.label_id is None:
        context.summary.skipped_no_label += 1
        context.issues.append(
            FrameSegmentReconciliationIssue(
                issue_type="skipped_no_label",
                track=track,
                video_id=segment_view.video_file_id,
                segment_id=segment_view.pk,
            )
        )
        return None
    frames = cast(
        list[_FrameLike],
        list(segment.get_frames().only("id", "frame_number")),
    )
    context.scope_frame_ids.update(frame.pk for frame in frames)
    if frames:
        return frames
    context.summary.skipped_no_frames += 1
    context.issues.append(
        FrameSegmentReconciliationIssue(
            issue_type="skipped_no_frames",
            track=track,
            video_id=segment_view.video_file_id,
            segment_id=segment_view.pk,
            label_id=segment_view.label_id,
        )
    )
    return None


def _segment_source_id_or_issue(
    context: _ReconciliationContext,
    segment_view: _SegmentLike,
    track: str,
) -> int | None:
    if track == "prediction":
        prediction_source = _prediction_annotation_source(create=context.spec.apply)
        return prediction_source.pk if prediction_source else None
    if segment_view.source_id is not None:
        return segment_view.source_id
    context.summary.skipped_no_source += 1
    context.issues.append(
        FrameSegmentReconciliationIssue(
            issue_type="skipped_no_source",
            track=track,
            video_id=segment_view.video_file_id,
            segment_id=segment_view.pk,
            label_id=segment_view.label_id,
        )
    )
    return None


def _eligible_segment(
    context: _ReconciliationContext,
    segment: LabelVideoSegment,
) -> _EligibleSegment | None:
    segment_view = cast(_SegmentLike, segment)
    context.scope_video_ids.add(segment_view.video_file_id)
    track = _segment_track(segment)
    if not _track_allowed(context.spec.track, track):
        return None
    context.summary.eligible_segments += 1
    frames = _segment_frames_or_issue(context, segment, segment_view, track)
    if frames is None:
        return None
    create_source_id = _segment_source_id_or_issue(context, segment_view, track)
    if create_source_id is None:
        return None
    return _EligibleSegment(
        segment=segment,
        segment_view=segment_view,
        track=track,
        frames=frames,
        model_meta_id=_segment_model_meta_id(segment),
        create_source_id=create_source_id,
    )


def _existing_annotations_by_key(
    context: _ReconciliationContext,
    eligible: _EligibleSegment,
) -> dict[AnnotationMatchKey, list[ImageClassificationAnnotation]]:
    existing_annotations = _existing_annotations_for_segment(
        segment=eligible.segment,
        frame_ids=[frame.pk for frame in eligible.frames],
        track=eligible.track,
        model_meta_id=eligible.model_meta_id,
        annotator=context.spec.annotator,
    )
    existing_by_key: dict[AnnotationMatchKey, list[ImageClassificationAnnotation]] = {}
    for annotation in existing_annotations:
        key = _annotation_key(annotation, track=eligible.track)
        existing_by_key.setdefault(key, []).append(annotation)
    return existing_by_key


def _record_legacy_match(
    context: _ReconciliationContext,
    eligible: _EligibleSegment,
    frame: _FrameLike,
    matching_annotations: list[ImageClassificationAnnotation],
) -> None:
    legacy_matches = [
        annotation
        for annotation in matching_annotations
        if not is_segment_derived_external_annotation_id(
            annotation.external_annotation_id
        )
    ]
    if not legacy_matches:
        return
    context.summary.legacy_matched_annotations += 1
    matching_annotation = cast(_AnnotationLike, legacy_matches[0])
    context.issues.append(
        FrameSegmentReconciliationIssue(
            issue_type="legacy_matched_annotation",
            track=eligible.track,
            video_id=eligible.segment_view.video_file_id,
            segment_id=eligible.segment_view.pk,
            annotation_id=matching_annotation.pk,
            frame_id=frame.pk,
            frame_number=frame.frame_number,
            label_id=eligible.segment_view.label_id,
            information_source_id=matching_annotation.information_source_id,
            model_meta_id=eligible.model_meta_id,
            annotator=_annotator_key(matching_annotation.annotator),
            external_annotation_id=matching_annotation.external_annotation_id,
            action="report_only",
        )
    )


def _missing_annotation(
    context: _ReconciliationContext,
    eligible: _EligibleSegment,
    frame: _FrameLike,
) -> ImageClassificationAnnotation:
    label_id = eligible.segment_view.label_id
    assert label_id is not None
    normalized_annotator = _normalize_annotator(context.spec.annotator)
    return ImageClassificationAnnotation(
        frame_id=frame.pk,
        label_id=label_id,
        value=True,
        information_source_id=eligible.create_source_id,
        model_meta_id=eligible.model_meta_id,
        annotator=normalized_annotator,
        external_annotation_id=segment_derived_external_annotation_id(
            segment_id=eligible.segment_view.pk,
            frame_id=frame.pk,
            label_id=label_id,
            information_source_id=eligible.create_source_id,
            model_meta_id=eligible.model_meta_id,
            annotator=normalized_annotator,
        ),
    )


def _record_missing_annotation(
    context: _ReconciliationContext,
    eligible: _EligibleSegment,
    frame: _FrameLike,
) -> None:
    context.summary.missing_annotations += 1
    action = "create" if context.spec.apply else "report_only"
    context.issues.append(
        FrameSegmentReconciliationIssue(
            issue_type="missing_annotation",
            track=eligible.track,
            video_id=eligible.segment_view.video_file_id,
            segment_id=eligible.segment_view.pk,
            frame_id=frame.pk,
            frame_number=frame.frame_number,
            label_id=eligible.segment_view.label_id,
            information_source_id=eligible.create_source_id,
            model_meta_id=eligible.model_meta_id,
            annotator=_annotator_key(context.spec.annotator),
            action=action,
        )
    )
    if context.spec.apply:
        context.annotations_to_create.append(
            _missing_annotation(context, eligible, frame)
        )


def _reconcile_expected_frame(
    context: _ReconciliationContext,
    eligible: _EligibleSegment,
    frame: _FrameLike,
    existing_by_key: dict[AnnotationMatchKey, list[ImageClassificationAnnotation]],
) -> None:
    label_id = eligible.segment_view.label_id
    assert label_id is not None
    context.summary.expected_annotations += 1
    expected_key = _expected_key(
        track=eligible.track,
        frame_id=frame.pk,
        label_id=label_id,
        information_source_id=eligible.segment_view.source_id,
        model_meta_id=eligible.model_meta_id,
        annotator=context.spec.annotator,
    )
    context.expected_keys.add(expected_key)
    matching_annotations = existing_by_key.get(expected_key, [])
    if matching_annotations:
        context.summary.matched_annotations += 1
        _record_legacy_match(context, eligible, frame, matching_annotations)
        return
    _record_missing_annotation(context, eligible, frame)


def _reconcile_segment(
    context: _ReconciliationContext,
    segment: LabelVideoSegment,
) -> None:
    eligible = _eligible_segment(context, segment)
    if eligible is None:
        return
    existing_by_key = _existing_annotations_by_key(context, eligible)
    for frame in eligible.frames:
        _reconcile_expected_frame(context, eligible, frame, existing_by_key)


def _scan_generated_annotations(context: _ReconciliationContext) -> None:
    annotations = _generated_annotation_queryset(
        spec=context.spec,
        scope_video_ids=context.scope_video_ids,
    )
    for annotation in annotations.iterator():
        annotation_view = cast(_AnnotationLike, annotation)
        track = _annotation_track(annotation)
        if not _track_allowed(context.spec.track, track):
            continue
        key = _annotation_key(annotation, track=track)
        if key in context.expected_keys:
            context.summary.generated_matched_annotations += 1
            continue
        context.summary.stale_generated_annotations += 1
        context.stale_annotation_ids.append(annotation.pk)
        context.issues.append(
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
                action="delete" if context.spec.apply else "report_only",
            )
        )


def _scan_unmarked_annotations(context: _ReconciliationContext) -> None:
    annotations = _unmarked_annotation_queryset(
        spec=context.spec,
        scope_video_ids=context.scope_video_ids,
        scope_frame_ids=context.scope_frame_ids,
    )
    for annotation in annotations.iterator():
        annotation_view = cast(_AnnotationLike, annotation)
        track = _annotation_track(annotation)
        if not _track_allowed(context.spec.track, track):
            continue
        if _annotation_key(annotation, track=track) in context.expected_keys:
            continue
        context.summary.suspicious_unmarked_annotations += 1
        context.issues.append(
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


def _create_missing_annotations(context: _ReconciliationContext) -> None:
    if not context.annotations_to_create:
        return
    ImageClassificationAnnotation.objects.bulk_create(
        context.annotations_to_create,
        ignore_conflicts=True,
    )
    context.summary.created_annotations = len(context.annotations_to_create)


def _delete_stale_annotations(context: _ReconciliationContext) -> None:
    if not context.stale_annotation_ids:
        return
    deleted, _ = ImageClassificationAnnotation.objects.filter(
        pk__in=context.stale_annotation_ids,
        external_annotation_id__startswith=(
            f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
        ),
    ).delete()
    context.summary.deleted_stale_generated_annotations = deleted


def _apply_reconciliation(context: _ReconciliationContext) -> None:
    if not context.spec.apply:
        return
    if not context.annotations_to_create and not context.stale_annotation_ids:
        return
    with transaction.atomic():
        _create_missing_annotations(context)
        _delete_stale_annotations(context)


def reconcile_frame_segment_annotations(
    spec: FrameSegmentReconciliationSpec,
) -> FrameSegmentReconciliationReport:
    if spec.track not in VALID_FRAME_SEGMENT_TRACKS:
        raise ValueError("track must be one of: all, manual, prediction.")
    context = _ReconciliationContext(
        spec=spec,
        scope_video_ids=set(spec.video_ids),
    )
    segments = _selected_segments(spec)
    context.summary.total_segments = len(segments)
    for segment in segments:
        _reconcile_segment(context, segment)
    _scan_generated_annotations(context)
    _scan_unmarked_annotations(context)
    _apply_reconciliation(context)
    return FrameSegmentReconciliationReport(
        spec=spec,
        summary=context.summary,
        issues=context.issues,
    )
