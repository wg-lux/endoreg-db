from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
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
    build_video_stream_path,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission


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


def _segment_category(segment: LabelVideoSegment) -> str | None:
    label_name = (
        cast(str, getattr(getattr(segment, "label", None), "name", "")) or ""
    ).lower()
    patient_findings = list(segment.patient_findings.all())
    finding_names = [
        (cast(str, getattr(getattr(pf, "finding", None), "name", "")) or "").lower()
        for pf in patient_findings
    ]
    has_polyp = ("polyp" in label_name) or any(
        "polyp" in name for name in finding_names
    )
    if has_polyp:
        return "polyp"

    has_intervention_label = "intervention" in label_name
    has_active_intervention = any(
        bool(getattr(intervention, "is_active", False))
        for pf in patient_findings
        for intervention in cast(
            _PatientFindingInterventionsSource,
            pf,
        ).interventions.all()
    )
    if has_intervention_label or has_active_intervention:
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


def _video_timestamp(video: VideoFile) -> tuple[datetime | None, str, bool]:
    sm = cast(SensitiveMeta | None, getattr(video, "sensitive_meta", None))
    if sm is not None:
        sensitive_meta_date = cast(
            dt_date | None, getattr(sm, "examination_date", None)
        )
        sensitive_meta_time = cast(
            dt_time | None, getattr(sm, "examination_time", None)
        )
        if sensitive_meta_date is not None:
            return (
                _combine_date_time(sensitive_meta_date, sensitive_meta_time),
                "examination_date",
                True,
            )

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
    if anonymized_text is not None and anonymized_text.strip():
        return anonymized_text

    full_report = cast(
        AnonymExaminationReport | None, getattr(pdf, "anonym_examination_report", None)
    )
    full_report_text = cast(str | None, getattr(full_report, "text", None))
    if full_report_text is not None and full_report_text.strip():
        return full_report_text

    sensitive_meta = cast(SensitiveMeta | None, getattr(pdf, "sensitive_meta", None))
    sensitive_text = cast(str | None, getattr(sensitive_meta, "anonymized_text", None))
    if sensitive_text is not None and sensitive_text.strip():
        return sensitive_text

    pdf_text = cast(str | None, getattr(pdf, "text", None))
    if pdf_text is not None and pdf_text.strip():
        return pdf_text
    return None


class PatientMediaTimelineView(APIView):
    """
    Combined media timeline for a patient.

    Endpoint:
        GET /api/media/patients/<patient_id>/timeline/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request: Request, patient_id: int) -> Response:
        try:
            patient = Patient.objects.get(pk=patient_id)
        except Patient.DoesNotExist:
            raise Http404(f"Patient with ID {patient_id} not found")

        params = _query_params(request)
        pe_filter_id = _query_int_param(params, "patient_examination_id")
        latest_only = _is_truthy(params.get("latest_only"))
        if (
            params.get("patient_examination_id") not in (None, "")
            and pe_filter_id is None
        ):
            return Response(
                {"detail": "patient_examination_id must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items: list[TimelineItem] = []

        reports_qs: QuerySet[AnonymExaminationReport] = (
            AnonymExaminationReport.objects.select_related(
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
        )
        if pe_filter_id is not None:
            reports_qs = reports_qs.filter(patient_examination_id=pe_filter_id)
        for report in reports_qs.distinct().all():
            ts, source, is_exam = _report_timestamp(report)
            report_file = cast(FieldFile | None, getattr(report, "file", None))
            report_file_name = cast(str | None, getattr(report_file, "name", None))

            raw_pdf_obj = cast(RawPdfFile | None, getattr(report, "raw_pdf_file", None))
            raw_pdf_id = cast(int | None, getattr(raw_pdf_obj, "pk", None))
            direct_patient = cast(Patient | None, getattr(report, "patient", None))
            report_sensitive_meta = cast(
                SensitiveMeta | None, getattr(report, "sensitive_meta", None)
            )
            pseudo_patient = cast(
                Patient | None,
                getattr(report_sensitive_meta, "pseudo_patient", None),
            )
            patient_examination = cast(
                PatientExamination | None,
                getattr(report, "patient_examination", None),
            )
            exam_patient = cast(
                Patient | None,
                getattr(patient_examination, "patient", None),
            )
            patient_examination_id = cast(
                int | None, getattr(patient_examination, "pk", None)
            )
            report_center = cast(Center | None, getattr(report, "center", None))
            report_center_name = cast(str | None, getattr(report_center, "name", None))
            full_report = cast(
                AnonymExaminationReport | None,
                getattr(report, "anonym_examination_report", None),
            )

            document_owner = full_report if full_report is not None else report
            document_type = cast(
                str | None,
                getattr(getattr(document_owner, "type", None), "name", None),
            )
            report_text = cast(str | None, getattr(report, "text", None))
            report_date = cast(dt_date | None, getattr(report, "date", None))
            report_time = cast(dt_time | None, getattr(report, "time", None))
            report_patient_id = cast(int | None, getattr(report, "patient_id", None))
            report_pseudo_patient_id = (
                cast(
                    int | None,
                    getattr(report_sensitive_meta, "pseudo_patient_id", None),
                )
                if report_sensitive_meta is not None
                else None
            )
            report_examination_patient_id = (
                cast(int | None, getattr(patient_examination, "patient_id", None))
                if patient_examination is not None
                else None
            )
            linked_source: list[TimelineValue] = [
                source_name
                for source_name, value in (
                    ("patient", report_patient_id),
                    ("sensitive_meta.pseudo_patient", report_pseudo_patient_id),
                    ("patient_examination.patient", report_examination_patient_id),
                )
                if value is not None
            ]

            item: TimelineItem = {
                "media_type": "full_report",
                "id": cast(int | None, report.pk),
                "patient_id": patient_id,
                "timestamp": _safe_iso(ts),
                "timestamp_source": source,
                "timestamp_is_examination_date": is_exam,
                "examination_date": _safe_iso(report_date),
                "examination_time": _safe_iso(report_time),
                "center_name": report_center_name,
                "file_name": report_file_name,
                "raw_pdf_id": raw_pdf_id,
                "patient_examination_id": patient_examination_id,
                "document_type": document_type,
                "anonymized_text": (
                    report_text if isinstance(report_text, str) else None
                ),
                "linked_patient": _patient_ref(direct_patient),
                "pseudo_patient": _patient_ref(pseudo_patient),
                "examination_patient": _patient_ref(exam_patient),
                "patient_link_sources": linked_source,
            }
            if raw_pdf_obj is not None:
                item["stream_options"] = _pdf_stream_options(request, raw_pdf_obj)
            else:
                item["stream_options"] = []
            items.append(item)

        pdfs_qs: QuerySet[RawPdfFile] = RawPdfFile.objects.select_related(
            "patient",
            "sensitive_meta",
            "center",
            "anonym_examination_report",
            "anonym_examination_report__type",
        ).filter(
            Q(patient_id=patient_id) | Q(sensitive_meta__pseudo_patient_id=patient_id)
        )
        if pe_filter_id is not None:
            pdfs_qs = pdfs_qs.filter(
                Q(examination_id=pe_filter_id)
                | Q(anonym_examination_report__patient_examination_id=pe_filter_id)
            )
        for pdf in pdfs_qs.distinct().all():
            ts, source, is_exam = _pdf_timestamp(pdf)
            sm = cast(SensitiveMeta | None, getattr(pdf, "sensitive_meta", None))
            direct_patient = cast(Patient | None, getattr(pdf, "patient", None))
            pseudo_patient = cast(Patient | None, getattr(sm, "pseudo_patient", None))
            full_report = cast(
                AnonymExaminationReport | None,
                getattr(pdf, "anonym_examination_report", None),
            )
            pdf_center = cast(Center | None, getattr(pdf, "center", None))
            center_name = cast(str | None, getattr(pdf_center, "name", None))
            pdf_uuid = cast(UUID | None, getattr(pdf, "uuid", None))
            patient_examination = cast(
                PatientExamination | None,
                getattr(pdf, "examination", None),
            )
            patient_examination_id = cast(
                int | None, getattr(patient_examination, "pk", None)
            )
            file_obj = cast(FieldFile | None, getattr(pdf, "file", None))
            processed_file_obj = cast(
                FieldFile | None, getattr(pdf, "processed_file", None)
            )
            full_report_id = cast(int | None, getattr(full_report, "pk", None))
            if full_report_id is None:
                full_report_id = cast(
                    int | None,
                    getattr(pdf, "anonym_examination_report_id", None),
                )
            sm_pseudo_patient_id = (
                cast(int | None, getattr(sm, "pseudo_patient_id", None))
                if sm is not None
                else None
            )
            patient_link_sources: list[TimelineValue] = [
                source_name
                for source_name, value in (
                    ("patient", cast(int | None, getattr(pdf, "patient_id", None))),
                    ("sensitive_meta.pseudo_patient", sm_pseudo_patient_id),
                )
                if value is not None
            ]

            item: TimelineItem = {
                "media_type": "pdf",
                "id": pdf.pk,
                "uuid": str(pdf_uuid) if pdf_uuid is not None else None,
                "patient_id": patient_id,
                "timestamp": _safe_iso(ts),
                "timestamp_source": source,
                "timestamp_is_examination_date": is_exam,
                "examination_date": _safe_iso(
                    cast(dt_date | None, getattr(sm, "examination_date", None))
                ),
                "examination_time": _safe_iso(
                    cast(dt_time | None, getattr(sm, "examination_time", None))
                ),
                "date_created": _safe_iso(
                    cast(dt_date | datetime | None, getattr(pdf, "date_created", None))
                ),
                "center_name": center_name,
                "pdf_hash": cast(str | None, getattr(pdf, "pdf_hash", None)),
                "file_name": cast(str | None, getattr(file_obj, "name", None)),
                "processed_file_name": cast(
                    str | None,
                    getattr(processed_file_obj, "name", None),
                ),
                "full_report_id": full_report_id,
                "document_type": cast(
                    str | None,
                    getattr(getattr(full_report, "type", None), "name", None),
                ),
                "patient_examination_id": patient_examination_id,
                "anonymized_text": _resolved_pdf_anonymized_text(pdf),
                "linked_patient": _patient_ref(direct_patient),
                "pseudo_patient": _patient_ref(pseudo_patient),
                "patient_link_sources": patient_link_sources,
                "stream_options": _pdf_stream_options(request, pdf),
            }
            items.append(item)

        videos_qs: QuerySet[VideoFile] = VideoFile.objects.select_related(
            "patient", "sensitive_meta", "center", "examination", "state"
        ).filter(
            Q(patient_id=patient_id)
            | Q(sensitive_meta__pseudo_patient_id=patient_id)
            | Q(examination__patient_id=patient_id)
        )
        if pe_filter_id is not None:
            videos_qs = videos_qs.filter(examination_id=pe_filter_id)
        for video in videos_qs.distinct().all():
            ts, source, is_exam = _video_timestamp(video)
            sm = cast(SensitiveMeta | None, getattr(video, "sensitive_meta", None))
            direct_patient = cast(Patient | None, getattr(video, "patient", None))
            pseudo_patient = cast(Patient | None, getattr(sm, "pseudo_patient", None))
            exam_patient = cast(
                Patient | None,
                getattr(
                    cast(
                        PatientExamination | None, getattr(video, "examination", None)
                    ),
                    "patient",
                    None,
                ),
            )
            active_file_name = _active_video_file_name(video)
            center_name = cast(
                str | None, getattr(getattr(video, "center", None), "name", None)
            )
            examination_id = cast(
                int | None,
                getattr(
                    cast(
                        PatientExamination | None, getattr(video, "examination", None)
                    ),
                    "pk",
                    None,
                ),
            )
            video_uuid = cast(UUID | None, getattr(video, "uuid", None))
            sm_examination_date = (
                cast(dt_date | None, getattr(sm, "examination_date", None))
                if sm is not None
                else None
            )
            sm_examination_time = (
                cast(dt_time | None, getattr(sm, "examination_time", None))
                if sm is not None
                else None
            )
            sm_pseudo_patient_id = (
                cast(int | None, getattr(sm, "pseudo_patient_id", None))
                if sm is not None
                else None
            )
            video_examination_patient_id = (
                cast(
                    int | None,
                    getattr(
                        cast(
                            PatientExamination | None,
                            getattr(video, "examination", None),
                        ),
                        "patient_id",
                        None,
                    ),
                )
                if getattr(video, "examination", None) is not None
                else None
            )
            video_date = cast(dt_date | None, getattr(video, "date", None))
            uploaded_at = cast(datetime | None, getattr(video, "uploaded_at", None))
            date_created = cast(datetime | None, getattr(video, "date_created", None))
            stream_source_id = video.pk
            video_timestamp = _safe_iso(ts)
            item: TimelineItem = {
                "media_type": "video",
                "id": cast(int | None, video.pk),
                "uuid": str(video_uuid) if video_uuid is not None else None,
                "patient_id": patient_id,
                "timestamp": video_timestamp,
                "timestamp_source": source,
                "timestamp_is_examination_date": is_exam,
                "examination_date": _safe_iso(sm_examination_date or video_date),
                "examination_time": _safe_iso(sm_examination_time),
                "uploaded_at": _safe_iso(uploaded_at),
                "date_created": _safe_iso(date_created),
                "center_name": center_name,
                "video_hash": cast(str | None, getattr(video, "video_hash", None)),
                "file_name": active_file_name,
                "original_file_name": cast(
                    str | None, getattr(video, "original_file_name", None)
                ),
                "patient_examination_id": examination_id,
                "linked_patient": _patient_ref(direct_patient),
                "pseudo_patient": _patient_ref(pseudo_patient),
                "examination_patient": _patient_ref(exam_patient),
                "patient_link_sources": [
                    source_name
                    for source_name, value in (
                        (
                            "patient",
                            cast(int | None, getattr(video, "patient_id", None)),
                        ),
                        ("sensitive_meta.pseudo_patient", sm_pseudo_patient_id),
                        (
                            "examination.patient",
                            video_examination_patient_id,
                        ),
                    )
                    if value is not None
                ],
                "stream_options": [
                    {
                        "type": "raw",
                        "url": build_absolute_media_url(
                            request,
                            build_video_stream_path(stream_source_id, file_type="raw"),
                        ),
                    },
                    {
                        "type": "processed",
                        "url": build_absolute_media_url(
                            request,
                            build_video_stream_path(
                                stream_source_id, file_type="processed"
                            ),
                        ),
                    },
                ],
            }
            items.append(item)

        def _sort_key(item_value: TimelineItem) -> tuple[int, datetime, int]:
            raw_ts = item_value.get("timestamp")
            item_id = item_value.get("id")
            item_id_int = cast(int, item_id) if isinstance(item_id, int) else 0
            if isinstance(raw_ts, str):
                try:
                    return (
                        1,
                        _make_aware_if_needed(datetime.fromisoformat(raw_ts)),
                        item_id_int,
                    )
                except ValueError:
                    pass
            return (
                0,
                _make_aware_if_needed(datetime.min),
                item_id_int,
            )

        items.sort(key=_sort_key, reverse=True)

        if latest_only:
            latest_report = next(
                (
                    item
                    for item in items
                    if item.get("media_type") in {"pdf", "full_report"}
                ),
                None,
            )
            latest_video = next(
                (item for item in items if item.get("media_type") == "video"),
                None,
            )

            latest_frames: list[TimelineItem] = []
            latest_video_id = cast(
                int | None,
                latest_video.get("id") if isinstance(latest_video, dict) else None,
            )
            if latest_video_id is not None:
                seen_frame_numbers: set[int] = set()
                segment_qs = (
                    LabelVideoSegment.objects.filter(video_file_id=latest_video_id)
                    .select_related("label")
                    .prefetch_related(
                        "patient_findings",
                        "patient_findings__finding",
                        "patient_findings__interventions",
                    )
                    .order_by("-start_frame_number", "-id")
                )

                prioritized_segments: dict[str, LabelVideoSegment] = {}
                for segment in segment_qs:
                    category = _segment_category(segment)
                    if category is not None and category not in prioritized_segments:
                        prioritized_segments[category] = segment
                    if len(prioritized_segments) == 3:
                        break

                for category in ("polyp", "intervention", "other_findings"):
                    selected_segment = prioritized_segments.get(category)
                    if selected_segment is None:
                        continue
                    frame_number = _segment_preferred_frame_number(selected_segment)
                    if frame_number is None or frame_number in seen_frame_numbers:
                        continue
                    seen_frame_numbers.add(frame_number)
                    segment_label = cast(
                        str | None,
                        getattr(
                            cast(
                                Label | None, getattr(selected_segment, "label", None)
                            ),
                            "name",
                            None,
                        ),
                    )
                    latest_frames.append(
                        {
                            "id": None,
                            "video_id": latest_video_id,
                            "frame_number": frame_number,
                            "timestamp": None,
                            "category": category,
                            "selection_source": "segment_priority",
                            "segment_id": cast(int | None, selected_segment.pk),
                            "segment_label": segment_label,
                            "stream_url": build_absolute_media_url(
                                request,
                                build_video_frame_stream_path(
                                    latest_video_id, frame_number
                                ),
                            ),
                        }
                    )

                remaining = 3 - len(latest_frames)
                if remaining > 0:
                    frame_qs = (
                        Frame.objects.filter(video_id=latest_video_id)
                        .order_by("-frame_number")
                        .only("id", "video_id", "frame_number", "timestamp")
                    )[:12]
                    for frame in frame_qs:
                        frame_number = cast(
                            int | None, getattr(frame, "frame_number", None)
                        )
                        if frame_number is None or frame_number in seen_frame_numbers:
                            continue
                        seen_frame_numbers.add(frame_number)
                        latest_frames.append(
                            {
                                "id": cast(int | None, frame.pk),
                                "video_id": latest_video_id,
                                "frame_number": frame_number,
                                "timestamp": cast(
                                    TimelineValue,
                                    cast(
                                        float | None, getattr(frame, "timestamp", None)
                                    ),
                                ),
                                "category": "fallback_latest",
                                "selection_source": "latest_frame",
                                "segment_id": None,
                                "segment_label": None,
                                "stream_url": build_absolute_media_url(
                                    request,
                                    build_video_frame_stream_path(
                                        latest_video_id,
                                        frame_number,
                                    ),
                                ),
                            }
                        )
                        if len(latest_frames) >= 3:
                            break

            return Response(
                {
                    "patient": {
                        "id": cast(int | None, patient.pk),
                        "first_name": cast(
                            str | None, getattr(patient, "first_name", None)
                        ),
                        "last_name": cast(
                            str | None, getattr(patient, "last_name", None)
                        ),
                        "dob": _safe_iso(
                            cast(dt_date | None, getattr(patient, "dob", None))
                        ),
                        "is_real_person": bool(
                            getattr(patient, "is_real_person", True)
                        ),
                        "patient_hash": cast(
                            str | None, getattr(patient, "patient_hash", None)
                        ),
                    },
                    "latest_report": latest_report,
                    "latest_video": latest_video,
                    "latest_frames": latest_frames,
                }
            )

        return Response(
            {
                "patient": {
                    "id": cast(int | None, patient.pk),
                    "first_name": cast(
                        str | None, getattr(patient, "first_name", None)
                    ),
                    "last_name": cast(str | None, getattr(patient, "last_name", None)),
                    "dob": _safe_iso(
                        cast(dt_date | None, getattr(patient, "dob", None))
                    ),
                    "is_real_person": bool(getattr(patient, "is_real_person", True)),
                    "patient_hash": cast(
                        str | None, getattr(patient, "patient_hash", None)
                    ),
                },
                "count": len(items),
                "results": items,
            }
        )
