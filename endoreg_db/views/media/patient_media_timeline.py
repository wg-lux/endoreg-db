from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import date as dt_date, datetime, time as dt_time
from uuid import UUID
from typing import Protocol, TypeAlias, cast

from django.db.models import Q
from django.db.models.fields.files import FieldFile
from django.db.models.query import QuerySet
from django.utils import timezone
from django.http import Http404
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.administration.person.patient.patient import Patient
from endoreg_db.models.label.label_video_segment.label_video_segment import (
    LabelVideoSegment,
)
from endoreg_db.models.label.label import Label
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_file import AnonymExaminationReport
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.video_files import get_active_video_file
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_pdf_stream_path,
    build_video_frame_stream_path,
    build_video_hls_playlist_path,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.views.access_control import assert_center_scope_allowed


class _InterventionRows(Protocol):
    def all(self) -> Iterable[object]: ...


class _PatientFindingInterventionsSource(Protocol):
    interventions: _InterventionRows


logger = logging.getLogger(__name__)

QueryValue: TypeAlias = str | None
TimelineScalar: TypeAlias = str | int | float | bool | dt_date | datetime
TimelineValue: TypeAlias = (
    TimelineScalar | None | list["TimelineValue"] | dict[str, "TimelineValue"]
)
TimelineItem: TypeAlias = dict[str, TimelineValue]


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: int | str | None) -> int | None:
    if isinstance(value, int):
        return value
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _make_aware_if_needed(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _combine_date_time(date_value: dt_date, time_value: dt_time | None) -> datetime:
    return _make_aware_if_needed(
        datetime.combine(date_value, time_value or dt_time.min)
    )


def _safe_iso(value: dt_date | datetime | dt_time | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _make_aware_if_needed(value).isoformat()
    if isinstance(value, dt_date):
        return value.isoformat()
    return str(value)


def _field_file_has_name(field_file: object | None) -> bool:
    name = getattr(field_file, "name", None)
    return isinstance(name, str) and bool(name)


def _pdf_stream_options(
    request: Request,
    pdf: RawPdfFile,
) -> list[TimelineValue]:
    pdf_id = pdf.pk
    options: list[TimelineValue] = []
    if _field_file_has_name(getattr(pdf, "file", None)):
        options.append(
            {
                "type": "raw",
                "url": build_absolute_media_url(
                    request,
                    build_pdf_stream_path(pdf_id, file_type="raw"),
                ),
            }
        )
    if _field_file_has_name(getattr(pdf, "processed_file", None)):
        options.append(
            {
                "type": "processed",
                "url": build_absolute_media_url(
                    request,
                    build_pdf_stream_path(pdf_id, file_type="processed"),
                ),
            }
        )
    return options


def _query_params(request: Request) -> Mapping[str, QueryValue]:
    return cast(Mapping[str, QueryValue], request.query_params)


def _query_int_param(params: Mapping[str, QueryValue], key: str) -> int | None:
    raw = params.get(key)
    if raw in ("", None):
        return None
    return _as_int(raw)


def _patient_ref(patient_obj: Patient | None) -> dict[str, TimelineValue] | None:
    if patient_obj is None:
        return None
    return {
        "id": cast(int | None, patient_obj.pk),
        "is_real_person": bool(getattr(patient_obj, "is_real_person", True)),
        "patient_hash": cast(str | None, getattr(patient_obj, "patient_hash", None)),
        "first_name": cast(str | None, getattr(patient_obj, "first_name", None)),
        "last_name": cast(str | None, getattr(patient_obj, "last_name", None)),
        "dob": _safe_iso(cast(dt_date | None, getattr(patient_obj, "dob", None))),
    }


def _active_video_file_name(video: VideoFile) -> str | None:
    try:
        active_file = get_active_video_file(video)
    except ValueError as exc:
        logger.debug(
            "Timeline video %s has no active file: %s",
            getattr(video, "pk", None),
            exc,
        )
        return None
    return cast(str | None, getattr(active_file, "name", None)) or None


def _segment_label_name(segment: LabelVideoSegment) -> str:
    return (
        cast(str, getattr(getattr(segment, "label", None), "name", "")) or ""
    ).lower()


def _segment_has_polyp(
    label_name: str,
    patient_findings: Sequence[object],
) -> bool:
    finding_names = [
        (cast(str, getattr(getattr(pf, "finding", None), "name", "")) or "").lower()
        for pf in patient_findings
    ]
    return ("polyp" in label_name) or any("polyp" in name for name in finding_names)


def _segment_has_active_intervention(patient_findings: Sequence[object]) -> bool:
    return any(
        bool(getattr(intervention, "is_active", False))
        for pf in patient_findings
        for intervention in cast(
            _PatientFindingInterventionsSource,
            pf,
        ).interventions.all()
    )


def _segment_category(segment: LabelVideoSegment) -> str | None:
    label_name = _segment_label_name(segment)
    patient_findings = list(segment.patient_findings.all())
    if _segment_has_polyp(label_name, patient_findings):
        return "polyp"
    if "intervention" in label_name or _segment_has_active_intervention(
        patient_findings
    ):
        return "intervention"
    if patient_findings:
        return "other_findings"
    return None


def _segment_preferred_frame_number(segment: LabelVideoSegment) -> int | None:
    start_n = _as_int(getattr(segment, "start_frame_number", None))
    end_n = _as_int(getattr(segment, "end_frame_number", None))
    if start_n is None or end_n is None:
        return None
    if end_n <= start_n:
        return None
    return max(start_n, end_n - 1)


def _report_timestamp(
    report: AnonymExaminationReport,
) -> tuple[datetime | None, str, bool]:
    report_date_raw = cast(dt_date | None, getattr(report, "date", None))
    if report_date_raw is not None:
        return (_combine_date_time(report_date_raw, None), "examination_date", True)

    sensitive_meta = cast(SensitiveMeta | None, getattr(report, "sensitive_meta", None))
    examination_date = (
        cast(dt_date | None, getattr(sensitive_meta, "examination_date", None))
        if sensitive_meta is not None
        else None
    )
    examination_time = (
        cast(dt_time | None, getattr(sensitive_meta, "examination_time", None))
        if sensitive_meta is not None
        else None
    )
    if examination_date is not None:
        return (
            _combine_date_time(examination_date, examination_time),
            "sensitive_meta_examination_date",
            True,
        )
    return None, "missing_examination_timestamp", False


def _pdf_timestamp(pdf: RawPdfFile) -> tuple[datetime | None, str, bool]:
    sm = cast(SensitiveMeta | None, getattr(pdf, "sensitive_meta", None))
    if sm is not None:
        examination_date = cast(dt_date | None, getattr(sm, "examination_date", None))
        if examination_date is not None:
            examination_time = cast(
                dt_time | None, getattr(sm, "examination_time", None)
            )
            return (
                _combine_date_time(examination_date, examination_time),
                "examination_date",
                True,
            )
    date_created_raw = cast(
        dt_date | datetime | None, getattr(pdf, "date_created", None)
    )
    if isinstance(date_created_raw, datetime):
        return _make_aware_if_needed(date_created_raw), "date_created", False
    return None, "missing_timestamp", False


def _sensitive_meta_timestamp(
    sensitive_meta: SensitiveMeta | None,
) -> tuple[datetime, str, bool] | None:
    if sensitive_meta is None:
        return None
    examination_date = cast(
        dt_date | None,
        getattr(sensitive_meta, "examination_date", None),
    )
    if examination_date is None:
        return None
    examination_time = cast(
        dt_time | None,
        getattr(sensitive_meta, "examination_time", None),
    )
    return (
        _combine_date_time(examination_date, examination_time),
        "examination_date",
        True,
    )


def _video_timestamp(video: VideoFile) -> tuple[datetime | None, str, bool]:
    sm = cast(SensitiveMeta | None, getattr(video, "sensitive_meta", None))
    sensitive_timestamp = _sensitive_meta_timestamp(sm)
    if sensitive_timestamp is not None:
        return sensitive_timestamp

    video_date = cast(dt_date | None, getattr(video, "date", None))
    if video_date is not None:
        return _combine_date_time(video_date, None), "video_date", True

    uploaded_at = cast(datetime | None, getattr(video, "uploaded_at", None))
    if uploaded_at is not None:
        return _make_aware_if_needed(uploaded_at), "uploaded_at", False
    date_created = cast(datetime | None, getattr(video, "date_created", None))
    if date_created is not None:
        return _make_aware_if_needed(date_created), "date_created", False
    return None, "missing_timestamp", False


def _resolved_pdf_anonymized_text(pdf: RawPdfFile) -> str | None:
    anonymized_text = cast(str | None, getattr(pdf, "anonymized_text", None))
    full_report = cast(
        AnonymExaminationReport | None, getattr(pdf, "anonym_examination_report", None)
    )
    full_report_text = cast(str | None, getattr(full_report, "text", None))
    sensitive_meta = cast(SensitiveMeta | None, getattr(pdf, "sensitive_meta", None))
    sensitive_text = cast(str | None, getattr(sensitive_meta, "anonymized_text", None))
    pdf_text = cast(str | None, getattr(pdf, "text", None))
    for candidate in (
        anonymized_text,
        full_report_text,
        sensitive_text,
        pdf_text,
    ):
        if candidate is not None and candidate.strip():
            return candidate
    return None


def _timeline_sort_key(item: TimelineItem) -> tuple[int, datetime, int]:
    raw_timestamp = item.get("timestamp")
    item_id = item.get("id")
    item_id_int = cast(int, item_id) if isinstance(item_id, int) else 0
    if isinstance(raw_timestamp, str):
        try:
            return (
                1,
                _make_aware_if_needed(datetime.fromisoformat(raw_timestamp)),
                item_id_int,
            )
        except ValueError:
            pass
    return 0, _make_aware_if_needed(datetime.min), item_id_int


def _patient_payload(patient: Patient) -> dict[str, TimelineValue]:
    return {
        "id": cast(int | None, patient.pk),
        "first_name": cast(str | None, getattr(patient, "first_name", None)),
        "last_name": cast(str | None, getattr(patient, "last_name", None)),
        "dob": _safe_iso(cast(dt_date | None, getattr(patient, "dob", None))),
        "is_real_person": bool(getattr(patient, "is_real_person", True)),
        "patient_hash": cast(str | None, getattr(patient, "patient_hash", None)),
    }


def _prioritized_segments(video_id: int) -> dict[str, LabelVideoSegment]:
    segment_qs = (
        LabelVideoSegment.objects.filter(video_file_id=video_id)
        .select_related("label")
        .prefetch_related(
            "patient_findings",
            "patient_findings__finding",
            "patient_findings__interventions",
        )
        .order_by("-start_frame_number", "-id")
    )
    prioritized: dict[str, LabelVideoSegment] = {}
    for segment in segment_qs:
        category = _segment_category(segment)
        if category is not None and category not in prioritized:
            prioritized[category] = segment
        if len(prioritized) == 3:
            break
    return prioritized


def _segment_frame_item(
    request: Request,
    *,
    video_id: int,
    category: str,
    segment: LabelVideoSegment,
) -> TimelineItem | None:
    frame_number = _segment_preferred_frame_number(segment)
    if frame_number is None:
        return None
    segment_label = cast(
        str | None,
        getattr(cast(Label | None, getattr(segment, "label", None)), "name", None),
    )
    return {
        "id": None,
        "video_id": video_id,
        "frame_number": frame_number,
        "timestamp": None,
        "category": category,
        "selection_source": "segment_priority",
        "segment_id": cast(int | None, segment.pk),
        "segment_label": segment_label,
        "stream_url": build_absolute_media_url(
            request,
            build_video_frame_stream_path(video_id, frame_number),
        ),
    }


def _prioritized_frame_items(request: Request, video_id: int) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    seen_frame_numbers: set[int] = set()
    segments = _prioritized_segments(video_id)
    for category in ("polyp", "intervention", "other_findings"):
        segment = segments.get(category)
        if segment is None:
            continue
        item = _segment_frame_item(
            request,
            video_id=video_id,
            category=category,
            segment=segment,
        )
        if item is None:
            continue
        frame_number = _unused_frame_number(item, seen_frame_numbers)
        if frame_number is None:
            continue
        seen_frame_numbers.add(frame_number)
        items.append(item)
    return items


def _existing_frame_numbers(items: Sequence[TimelineItem]) -> set[int]:
    return {
        frame_number
        for item in items
        if isinstance((frame_number := item.get("frame_number")), int)
    }


def _unused_frame_number(
    item: TimelineItem,
    seen_frame_numbers: set[int],
) -> int | None:
    frame_number = item.get("frame_number")
    if not isinstance(frame_number, int) or frame_number in seen_frame_numbers:
        return None
    return frame_number


def _fallback_frame_item(
    request: Request,
    *,
    video_id: int,
    frame: Frame,
    frame_number: int,
) -> TimelineItem:
    return {
        "id": cast(int | None, frame.pk),
        "video_id": video_id,
        "frame_number": frame_number,
        "timestamp": cast(
            TimelineValue,
            cast(float | None, getattr(frame, "timestamp", None)),
        ),
        "category": "fallback_latest",
        "selection_source": "latest_frame",
        "segment_id": None,
        "segment_label": None,
        "stream_url": build_absolute_media_url(
            request,
            build_video_frame_stream_path(video_id, frame_number),
        ),
    }


def _fallback_frame_items(
    request: Request,
    *,
    video_id: int,
    existing_items: Sequence[TimelineItem],
) -> list[TimelineItem]:
    seen_frame_numbers = _existing_frame_numbers(existing_items)
    items: list[TimelineItem] = []
    frame_qs = (
        Frame.objects.filter(video_id=video_id)
        .order_by("-frame_number")
        .only("id", "video_id", "frame_number", "timestamp")
    )[:12]
    for frame in frame_qs:
        frame_number = cast(int | None, getattr(frame, "frame_number", None))
        if frame_number is None or frame_number in seen_frame_numbers:
            continue
        seen_frame_numbers.add(frame_number)
        items.append(
            _fallback_frame_item(
                request,
                video_id=video_id,
                frame=frame,
                frame_number=frame_number,
            )
        )
        if len(existing_items) + len(items) >= 3:
            break
    return items


def _latest_frame_items(
    request: Request,
    latest_video: TimelineItem | None,
) -> list[TimelineItem]:
    latest_video_id = cast(
        int | None,
        latest_video.get("id") if latest_video is not None else None,
    )
    if latest_video_id is None:
        return []
    items = _prioritized_frame_items(request, latest_video_id)
    items.extend(
        _fallback_frame_items(
            request,
            video_id=latest_video_id,
            existing_items=items,
        )
    )
    return items


def _latest_timeline_response(
    request: Request,
    *,
    patient: Patient,
    items: Sequence[TimelineItem],
) -> Response:
    latest_report = next(
        (item for item in items if item.get("media_type") in {"pdf", "full_report"}),
        None,
    )
    latest_video = next(
        (item for item in items if item.get("media_type") == "video"),
        None,
    )
    return Response(
        {
            "patient": _patient_payload(patient),
            "latest_report": latest_report,
            "latest_video": latest_video,
            "latest_frames": _latest_frame_items(request, latest_video),
        }
    )


def _linked_patient_sources(
    *sources: tuple[str, int | None],
) -> list[TimelineValue]:
    return [source_name for source_name, value in sources if value is not None]


def _report_queryset(
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> QuerySet[AnonymExaminationReport]:
    reports = AnonymExaminationReport.objects.select_related(
        "patient",
        "patient_examination",
        "sensitive_meta",
        "center",
        "type",
    ).filter(
        Q(patient_id=patient_id)
        | Q(sensitive_meta__pseudo_patient_id=patient_id)
        | Q(patient_examination__patient_id=patient_id)
    )
    if patient_examination_id is not None:
        reports = reports.filter(patient_examination_id=patient_examination_id)
    return reports.distinct()


def _report_timeline_item(
    request: Request,
    *,
    patient_id: int,
    report: AnonymExaminationReport,
) -> TimelineItem:
    timestamp, timestamp_source, timestamp_is_examination_date = _report_timestamp(
        report
    )
    report_file = cast(FieldFile | None, getattr(report, "file", None))
    raw_pdf = cast(RawPdfFile | None, getattr(report, "raw_pdf_file", None))
    direct_patient = cast(Patient | None, getattr(report, "patient", None))
    sensitive_meta = cast(
        SensitiveMeta | None,
        getattr(report, "sensitive_meta", None),
    )
    pseudo_patient = cast(
        Patient | None,
        getattr(sensitive_meta, "pseudo_patient", None),
    )
    patient_examination = cast(
        PatientExamination | None,
        getattr(report, "patient_examination", None),
    )
    examination_patient = cast(
        Patient | None,
        getattr(patient_examination, "patient", None),
    )
    full_report = cast(
        AnonymExaminationReport | None,
        getattr(report, "anonym_examination_report", None),
    )
    document_owner = full_report if full_report is not None else report
    report_text = cast(str | None, getattr(report, "text", None))
    return {
        "media_type": "full_report",
        "id": cast(int | None, report.pk),
        "patient_id": patient_id,
        "timestamp": _safe_iso(timestamp),
        "timestamp_source": timestamp_source,
        "timestamp_is_examination_date": timestamp_is_examination_date,
        "examination_date": _safe_iso(
            cast(dt_date | None, getattr(report, "date", None))
        ),
        "examination_time": _safe_iso(
            cast(dt_time | None, getattr(report, "time", None))
        ),
        "center_name": cast(
            str | None,
            getattr(
                cast(Center | None, getattr(report, "center", None)),
                "name",
                None,
            ),
        ),
        "file_name": cast(str | None, getattr(report_file, "name", None)),
        "raw_pdf_id": cast(int | None, getattr(raw_pdf, "pk", None)),
        "patient_examination_id": cast(
            int | None,
            getattr(patient_examination, "pk", None),
        ),
        "document_type": cast(
            str | None,
            getattr(getattr(document_owner, "type", None), "name", None),
        ),
        "anonymized_text": report_text if isinstance(report_text, str) else None,
        "linked_patient": _patient_ref(direct_patient),
        "pseudo_patient": _patient_ref(pseudo_patient),
        "examination_patient": _patient_ref(examination_patient),
        "patient_link_sources": _linked_patient_sources(
            ("patient", cast(int | None, getattr(report, "patient_id", None))),
            (
                "sensitive_meta.pseudo_patient",
                cast(
                    int | None,
                    getattr(sensitive_meta, "pseudo_patient_id", None),
                ),
            ),
            (
                "patient_examination.patient",
                cast(
                    int | None,
                    getattr(patient_examination, "patient_id", None),
                ),
            ),
        ),
        "stream_options": _pdf_stream_options(request, raw_pdf)
        if raw_pdf is not None
        else [],
    }


def _report_timeline_items(
    request: Request,
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> list[TimelineItem]:
    return [
        _report_timeline_item(request, patient_id=patient_id, report=report)
        for report in _report_queryset(
            patient_id=patient_id,
            patient_examination_id=patient_examination_id,
        )
    ]


def _pdf_queryset(
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> QuerySet[RawPdfFile]:
    pdfs = RawPdfFile.objects.select_related(
        "patient",
        "sensitive_meta",
        "center",
        "anonym_examination_report",
        "anonym_examination_report__type",
    ).filter(Q(patient_id=patient_id) | Q(sensitive_meta__pseudo_patient_id=patient_id))
    if patient_examination_id is not None:
        pdfs = pdfs.filter(
            Q(examination_id=patient_examination_id)
            | Q(
                anonym_examination_report__patient_examination_id=patient_examination_id
            )
        )
    return pdfs.distinct()


def _pdf_full_report_id(
    pdf: RawPdfFile,
    full_report: AnonymExaminationReport | None,
) -> int | None:
    full_report_id = cast(int | None, getattr(full_report, "pk", None))
    if full_report_id is not None:
        return full_report_id
    return cast(
        int | None,
        getattr(pdf, "anonym_examination_report_id", None),
    )


def _pdf_timeline_item(
    request: Request,
    *,
    patient_id: int,
    pdf: RawPdfFile,
) -> TimelineItem:
    timestamp, timestamp_source, timestamp_is_examination_date = _pdf_timestamp(pdf)
    sensitive_meta = cast(
        SensitiveMeta | None,
        getattr(pdf, "sensitive_meta", None),
    )
    direct_patient = cast(Patient | None, getattr(pdf, "patient", None))
    pseudo_patient = cast(
        Patient | None,
        getattr(sensitive_meta, "pseudo_patient", None),
    )
    full_report = cast(
        AnonymExaminationReport | None,
        getattr(pdf, "anonym_examination_report", None),
    )
    patient_examination = cast(
        PatientExamination | None,
        getattr(pdf, "examination", None),
    )
    pdf_uuid = cast(UUID | None, getattr(pdf, "uuid", None))
    file_obj = cast(FieldFile | None, getattr(pdf, "file", None))
    processed_file_obj = cast(
        FieldFile | None,
        getattr(pdf, "processed_file", None),
    )
    return {
        "media_type": "pdf",
        "id": cast(int | None, pdf.pk),
        "uuid": str(pdf_uuid) if pdf_uuid is not None else None,
        "patient_id": patient_id,
        "timestamp": _safe_iso(timestamp),
        "timestamp_source": timestamp_source,
        "timestamp_is_examination_date": timestamp_is_examination_date,
        "examination_date": _safe_iso(
            cast(dt_date | None, getattr(sensitive_meta, "examination_date", None))
        ),
        "examination_time": _safe_iso(
            cast(dt_time | None, getattr(sensitive_meta, "examination_time", None))
        ),
        "date_created": _safe_iso(
            cast(dt_date | datetime | None, getattr(pdf, "date_created", None))
        ),
        "center_name": cast(
            str | None,
            getattr(
                cast(Center | None, getattr(pdf, "center", None)),
                "name",
                None,
            ),
        ),
        "pdf_hash": cast(str | None, getattr(pdf, "pdf_hash", None)),
        "file_name": cast(str | None, getattr(file_obj, "name", None)),
        "processed_file_name": cast(
            str | None,
            getattr(processed_file_obj, "name", None),
        ),
        "full_report_id": _pdf_full_report_id(pdf, full_report),
        "document_type": cast(
            str | None,
            getattr(getattr(full_report, "type", None), "name", None),
        ),
        "patient_examination_id": cast(
            int | None,
            getattr(patient_examination, "pk", None),
        ),
        "anonymized_text": _resolved_pdf_anonymized_text(pdf),
        "linked_patient": _patient_ref(direct_patient),
        "pseudo_patient": _patient_ref(pseudo_patient),
        "patient_link_sources": _linked_patient_sources(
            ("patient", cast(int | None, getattr(pdf, "patient_id", None))),
            (
                "sensitive_meta.pseudo_patient",
                cast(
                    int | None,
                    getattr(sensitive_meta, "pseudo_patient_id", None),
                ),
            ),
        ),
        "stream_options": _pdf_stream_options(request, pdf),
    }


def _pdf_timeline_items(
    request: Request,
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> list[TimelineItem]:
    return [
        _pdf_timeline_item(request, patient_id=patient_id, pdf=pdf)
        for pdf in _pdf_queryset(
            patient_id=patient_id,
            patient_examination_id=patient_examination_id,
        )
    ]


def _video_queryset(
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> QuerySet[VideoFile]:
    videos = VideoFile.objects.select_related(
        "patient",
        "sensitive_meta",
        "center",
        "examination",
        "state",
    ).filter(
        Q(patient_id=patient_id)
        | Q(sensitive_meta__pseudo_patient_id=patient_id)
        | Q(examination__patient_id=patient_id)
    )
    if patient_examination_id is not None:
        videos = videos.filter(examination_id=patient_examination_id)
    return videos.distinct()


def _video_timeline_item(
    request: Request,
    *,
    patient_id: int,
    video: VideoFile,
) -> TimelineItem:
    timestamp, timestamp_source, timestamp_is_examination_date = _video_timestamp(video)
    sensitive_meta = cast(
        SensitiveMeta | None,
        getattr(video, "sensitive_meta", None),
    )
    direct_patient = cast(Patient | None, getattr(video, "patient", None))
    pseudo_patient = cast(
        Patient | None,
        getattr(sensitive_meta, "pseudo_patient", None),
    )
    patient_examination = cast(
        PatientExamination | None,
        getattr(video, "examination", None),
    )
    examination_patient = cast(
        Patient | None,
        getattr(patient_examination, "patient", None),
    )
    video_uuid = cast(UUID | None, getattr(video, "uuid", None))
    video_date = cast(dt_date | None, getattr(video, "date", None))
    return {
        "media_type": "video",
        "id": cast(int | None, video.pk),
        "uuid": str(video_uuid) if video_uuid is not None else None,
        "patient_id": patient_id,
        "timestamp": _safe_iso(timestamp),
        "timestamp_source": timestamp_source,
        "timestamp_is_examination_date": timestamp_is_examination_date,
        "examination_date": _safe_iso(
            cast(
                dt_date | None,
                getattr(sensitive_meta, "examination_date", None),
            )
            or video_date
        ),
        "examination_time": _safe_iso(
            cast(
                dt_time | None,
                getattr(sensitive_meta, "examination_time", None),
            )
        ),
        "uploaded_at": _safe_iso(
            cast(datetime | None, getattr(video, "uploaded_at", None))
        ),
        "date_created": _safe_iso(
            cast(datetime | None, getattr(video, "date_created", None))
        ),
        "center_name": cast(
            str | None,
            getattr(getattr(video, "center", None), "name", None),
        ),
        "video_hash": cast(str | None, getattr(video, "video_hash", None)),
        "file_name": _active_video_file_name(video),
        "original_file_name": cast(
            str | None,
            getattr(video, "original_file_name", None),
        ),
        "patient_examination_id": cast(
            int | None,
            getattr(patient_examination, "pk", None),
        ),
        "linked_patient": _patient_ref(direct_patient),
        "pseudo_patient": _patient_ref(pseudo_patient),
        "examination_patient": _patient_ref(examination_patient),
        "patient_link_sources": _linked_patient_sources(
            ("patient", cast(int | None, getattr(video, "patient_id", None))),
            (
                "sensitive_meta.pseudo_patient",
                cast(
                    int | None,
                    getattr(sensitive_meta, "pseudo_patient_id", None),
                ),
            ),
            (
                "examination.patient",
                cast(
                    int | None,
                    getattr(patient_examination, "patient_id", None),
                ),
            ),
        ),
        "stream_options": [
            {
                "type": "processed",
                "url": build_absolute_media_url(
                    request,
                    build_video_hls_playlist_path(
                        int(video.pk),
                        file_type="processed",
                    ),
                ),
            },
        ],
    }


def _video_timeline_items(
    request: Request,
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> list[TimelineItem]:
    return [
        _video_timeline_item(request, patient_id=patient_id, video=video)
        for video in _video_queryset(
            patient_id=patient_id,
            patient_examination_id=patient_examination_id,
        )
    ]


def _timeline_items(
    request: Request,
    *,
    patient_id: int,
    patient_examination_id: int | None,
) -> list[TimelineItem]:
    items = _report_timeline_items(
        request,
        patient_id=patient_id,
        patient_examination_id=patient_examination_id,
    )
    items.extend(
        _pdf_timeline_items(
            request,
            patient_id=patient_id,
            patient_examination_id=patient_examination_id,
        )
    )
    items.extend(
        _video_timeline_items(
            request,
            patient_id=patient_id,
            patient_examination_id=patient_examination_id,
        )
    )
    items.sort(key=_timeline_sort_key, reverse=True)
    return items


def _patient_or_404(patient_id: int) -> Patient:
    try:
        return Patient.objects.get(pk=patient_id)
    except Patient.DoesNotExist:
        raise Http404(f"Patient with ID {patient_id} not found")


def _invalid_patient_examination_response(
    params: Mapping[str, QueryValue],
    patient_examination_id: int | None,
) -> Response | None:
    if (
        params.get("patient_examination_id") not in (None, "")
        and patient_examination_id is None
    ):
        return Response(
            {"detail": "patient_examination_id must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _timeline_response(
    request: Request,
    *,
    patient: Patient,
    items: Sequence[TimelineItem],
    latest_only: bool,
) -> Response:
    if latest_only:
        return _latest_timeline_response(request, patient=patient, items=items)
    return Response(
        {
            "patient": _patient_payload(patient),
            "count": len(items),
            "results": items,
        }
    )


class PatientMediaTimelineView(APIView):
    """
    Combined media timeline for a patient.

    Endpoint:
        GET /api/media/patients/<patient_id>/timeline/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request: Request, patient_id: int) -> Response:
        patient = _patient_or_404(patient_id)
        assert_center_scope_allowed(request=request, obj=patient)

        params = _query_params(request)
        patient_examination_id = _query_int_param(params, "patient_examination_id")
        latest_only = _is_truthy(params.get("latest_only"))
        invalid_response = _invalid_patient_examination_response(
            params,
            patient_examination_id,
        )
        if invalid_response is not None:
            return invalid_response

        return _timeline_response(
            request,
            patient=patient,
            items=_timeline_items(
                request,
                patient_id=patient_id,
                patient_examination_id=patient_examination_id,
            ),
            latest_only=latest_only,
        )
