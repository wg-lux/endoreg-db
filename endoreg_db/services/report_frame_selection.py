"""Canonical report frame queries and locked selection persistence."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Case, IntegerField, Max, Q, QuerySet, Value, When
from django.utils import timezone
from lx_dtypes.models.contracts.patient_examination_report import (
    ReportFrameCandidate,
    ReportFrameCandidatesQuery,
    ReportFrameCandidatesResponse,
    ReportSegmentFrameSelectionData,
    ReportSegmentFrameSelectionPayload,
    ReportSegmentSelectionMap,
    dump_segment_frame_selection_payload,
    report_json_safe,
    report_json_safe_dict,
    validate_segment_selection_map,
)
from typing import cast
from endoreg_db.helpers.model_ids import model_pk
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.report.patient_examination_report import PatientExaminationReport
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.models.medical.finding.finding import Finding
from endoreg_db.models.state.frame_annotation_segment_identity import (
    MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES,
    PREDICTION_INFORMATION_SOURCE_NAMES,
    SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX,
)
from endoreg_db.schemas.report_persistence import ReportAutoSelectionPayload

SEGMENT_FRAME_SELECTIONS_KEY = "report_segment_frame_selections"


def patient_examination_segment_filter(patient_examination: PatientExamination) -> Q:
    """Keep direct examination ownership authoritative over shared metadata."""
    sensitive_meta_ids = set(
        RawPdfFile.objects.filter(
            examination_id=patient_examination.pk, sensitive_meta_id__isnull=False
        ).values_list("sensitive_meta_id", flat=True)
    )
    sensitive_meta_ids.update(
        AnonymExaminationReport.objects.filter(
            patient_examination_id=patient_examination.pk,
            sensitive_meta_id__isnull=False,
        ).values_list("sensitive_meta_id", flat=True)
    )
    return Q(video_file__examination_id=patient_examination.pk) | Q(
        Q(video_file__patient_id=patient_examination.patient_id)
        | Q(video_file__patient_id__isnull=True),
        video_file__examination_id__isnull=True,
        video_file__sensitive_meta_id__in=sensitive_meta_ids,
    )


def segment_patient_findings(
    segment: LabelVideoSegment,
    patient_examination: PatientExamination,
) -> list[PatientFinding]:
    return sorted(
        (
            finding
            for finding in segment.patient_findings.all()
            if finding.patient_examination_id == patient_examination.pk
            and finding.is_active
        ),
        key=lambda finding: finding.pk,
    )


def selected_segment_patient_finding(
    segment: LabelVideoSegment,
    patient_examination: PatientExamination,
    *,
    patient_finding_id: int | None = None,
) -> PatientFinding | None:
    """Resolve the selected lesion without guessing among linked findings."""
    candidates = segment_patient_findings(segment, patient_examination)
    if patient_finding_id is not None:
        for finding in candidates:
            if finding.pk == patient_finding_id:
                return finding
        raise ValueError(
            "Selected patient finding is not active and linked to this segment."
        )
    if len(candidates) > 1:
        raise ValueError(
            "Ambiguous segment findings; select patient_finding_id explicitly."
        )
    return candidates[0] if candidates else None


@transaction.atomic
def assign_segment_patient_finding(
    segment: LabelVideoSegment,
    patient_examination: PatientExamination,
    *,
    finding_id: int | None,
    patient_finding_id: int | None,
    user: User | None,
) -> PatientFinding | None:
    """Attach a concrete lesion, serializing legacy singleton creation with reports."""
    PatientExamination.objects.select_for_update().get(pk=patient_examination.pk)
    if not LabelVideoSegment.objects.filter(
        patient_examination_segment_filter(patient_examination), pk=segment.pk
    ).exists():
        raise ValueError("Segment does not belong to this patient examination.")
    candidates = PatientFinding.objects.filter(
        patient_examination=patient_examination, is_active=True
    ).select_related("finding")
    if patient_finding_id is not None:
        finding = candidates.filter(pk=patient_finding_id).first()
        if finding is None or (
            finding_id is not None and model_pk(finding.finding) != finding_id
        ):
            raise ValueError(
                "Patient finding does not match this examination and finding type."
            )
    elif finding_id is not None:
        matches = list(candidates.filter(finding_id=finding_id)[:2])
        if len(matches) > 1:
            raise ValueError(
                "Ambiguous finding type; select patient_finding_id explicitly."
            )
        finding = matches[0] if matches else None
        if finding is None:
            finding_type = Finding.objects.filter(pk=finding_id).first()
            if finding_type is None:
                raise ValueError("finding_id does not exist.")
            if (
                patient_examination.examination_id is not None
                and not Finding.objects.filter(
                    pk=finding_type.pk,
                    examinations__pk=patient_examination.examination_id,
                ).exists()
            ):
                raise ValueError("Finding is not allowed for this examination.")
            finding = PatientFinding.objects.create(
                patient_examination=patient_examination,
                finding=finding_type,
                created_by=user,
                updated_by=user,
            )
    else:
        return selected_segment_patient_finding(segment, patient_examination)
    if user is not None:
        finding.updated_by = user
        finding.save(update_fields=["updated_by", "updated_at"])
    segment.patient_findings.add(finding)
    return finding


def report_frame_candidates(
    examination_id: int,
    query: ReportFrameCandidatesQuery,
) -> ReportFrameCandidatesResponse:
    frames = Frame.objects.select_related("video").filter(
        video__examination_id=examination_id,
        video__state__anonymized=True,
        video__state__anonymization_validated=True,
        video__state__processing_error=False,
        timestamp__gte=0,
    )
    annotations = ImageClassificationAnnotation.objects.filter(
        frame__in=frames, value=True
    )
    labels = list(
        annotations.order_by("label__name")
        .values_list("label__name", flat=True)
        .distinct()
    )
    if query.label is not None:
        frames = frames.filter(
            pk__in=annotations.filter(label__name=query.label).values("frame_id")
        )
    rows = list(
        frames.order_by("video_id", "frame_number", "pk")[
            query.offset : query.offset + query.limit + 1
        ]
    )
    page = rows[: query.limit]
    labels_by_frame: dict[int, list[str]] = {}
    for frame_id, name in (
        annotations.filter(frame__in=page)
        .values_list("frame_id", "label__name")
        .distinct()
    ):
        labels_by_frame.setdefault(frame_id, []).append(name)
    return ReportFrameCandidatesResponse(
        frames=[
            ReportFrameCandidate(
                video_id=model_pk(frame.video),
                frame_number=frame.frame_number,
                timestamp=cast(float, frame.timestamp),
                labels=sorted(labels_by_frame.get(model_pk(frame), [])),
            )
            for frame in page
        ],
        labels=labels,
        next_offset=query.offset + query.limit if len(rows) > query.limit else None,
    )


def _save_selections(
    report: PatientExaminationReport,
    selections: ReportSegmentSelectionMap,
    user: User | None,
) -> None:
    payload = report_json_safe_dict(report.editor_payload)
    payload[SEGMENT_FRAME_SELECTIONS_KEY] = report_json_safe(selections)
    report.editor_payload = payload
    if user is not None:
        report.updated_by = user
    report.save(update_fields=["editor_payload", "updated_by", "updated_at"])


def persist_segment_selection(
    report: PatientExaminationReport,
    *,
    segment_id: int,
    selection: ReportSegmentFrameSelectionData | None,
    user: User | None,
) -> None:
    with transaction.atomic():
        locked = PatientExaminationReport.objects.select_for_update().get(pk=report.pk)
        selections = validate_segment_selection_map(
            locked.editor_payload.get(SEGMENT_FRAME_SELECTIONS_KEY, {})
        )
        if selection is None:
            selections.pop(str(segment_id), None)
        else:
            selections[str(segment_id)] = selection
        _save_selections(locked, selections, user)


def auto_select_report_frames(
    report: PatientExaminationReport,
    *,
    command: ReportAutoSelectionPayload,
    segments: QuerySet[LabelVideoSegment],
    user: User | None,
) -> None:
    """Fill empty segment selections from positive annotations; retain manual work."""
    if model_pk(report.patient_examination) != command.patient_examination_id:
        raise ValueError("Report does not belong to the requested examination")
    prefix = "image_classification_annotations__"
    human = Q(
        **{
            f"{prefix}information_source__name__in": MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES
        }
    ) | Q(
        **{
            f"{prefix}information_source__information_source_types__name__in": [
                "annotation",
                "manual_annotation",
            ]
        }
    )
    prediction = (
        Q(
            **{
                f"{prefix}information_source__name__in": PREDICTION_INFORMATION_SOURCE_NAMES
            }
        )
        | Q(
            **{
                f"{prefix}information_source__information_source_types__name": "prediction"
            }
        )
        | Q(**{f"{prefix}model_meta_id__isnull": False})
    )
    segment_prediction = prediction & Q(
        **{
            f"{prefix}external_annotation_id__startswith": f"{SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX}:"
        }
    )
    positive = Q(**{f"{prefix}value": True})
    with transaction.atomic():
        locked = PatientExaminationReport.objects.select_for_update().get(pk=report.pk)
        selections = validate_segment_selection_map(
            locked.editor_payload.get(SEGMENT_FRAME_SELECTIONS_KEY, {})
        )
        added = 0
        for segment in segments:
            key = str(model_pk(segment))
            if key in selections:
                continue
            frame = (
                Frame.objects.filter(
                    video=segment.video_file,
                    video__state__anonymized=True,
                    video__state__anonymization_validated=True,
                    video__state__processing_error=False,
                    timestamp__gte=0,
                    frame_number__gte=segment.start_frame_number,
                    frame_number__lt=segment.end_frame_number,
                )
                .annotate(
                    preference_score=Max(
                        Case(
                            When(positive & human, then=Value(3)),
                            When(positive & segment_prediction, then=Value(2)),
                            When(positive & prediction, then=Value(1)),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    )
                )
                .filter(preference_score__gt=0)
                .order_by("-preference_score", "frame_number", "pk")
                .first()
            )
            if frame is None:
                continue
            selections[key] = dump_segment_frame_selection_payload(
                ReportSegmentFrameSelectionPayload(
                    segment_id=model_pk(segment),
                    video_id=model_pk(segment.video_file),
                    frame_number=frame.frame_number,
                    frame_id=model_pk(frame),
                    relative_path=str(frame.relative_path)
                    if frame.relative_path
                    else None,
                    finding_id=None,
                    patient_finding_id=None,
                    updated_at=timezone.now().isoformat(),
                    selection_source="auto_populate",
                )
            )
            added += 1
            if added >= command.limit:
                break
        if added:
            _save_selections(locked, selections, user)
