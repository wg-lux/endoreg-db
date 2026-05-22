import logging
from datetime import date as dt_date, datetime, time as dt_time
from typing import Any

from django.http import Http404
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import (
    AnonymExaminationReport,
    Frame,
    LabelVideoSegment,
    Patient,
    RawPdfFile,
    VideoFile,
)
from endoreg_db.services.video_files import get_active_video_file
from endoreg_db.utils.web.media_urls import (
    build_absolute_media_url,
    build_pdf_stream_path,
    build_video_frame_stream_path,
    build_video_stream_path,
)
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _make_aware_if_needed(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _combine_date_time(date_value: dt_date, time_value: dt_time | None) -> datetime:
    return _make_aware_if_needed(
        datetime.combine(date_value, time_value or dt_time.min)
    )


def _safe_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _make_aware_if_needed(value).isoformat()
    if isinstance(value, dt_date):
        return value.isoformat()
    try:
        return str(value)
    except Exception:
        return None


def _patient_ref(patient_obj: Any) -> dict[str, Any] | None:
    if patient_obj is None:
        return None
    return {
        "id": getattr(patient_obj, "pk", None),
        "is_real_person": bool(getattr(patient_obj, "is_real_person", True)),
        "patient_hash": getattr(patient_obj, "patient_hash", None),
        "first_name": getattr(patient_obj, "first_name", None),
        "last_name": getattr(patient_obj, "last_name", None),
        "dob": _safe_iso(getattr(patient_obj, "dob", None)),
    }


def _segment_category(segment: LabelVideoSegment) -> str | None:
    label_name = (getattr(getattr(segment, "label", None), "name", "") or "").lower()
    patient_findings = list(segment.patient_findings.all())
    finding_names = [
        (getattr(getattr(pf, "finding", None), "name", "") or "").lower()
        for pf in patient_findings
    ]
    has_polyp = ("polyp" in label_name) or any(
        "polyp" in name for name in finding_names
    )
    if has_polyp:
        return "polyp"

    has_intervention_label = "intervention" in label_name
    has_active_intervention = any(
        intervention.is_active
        for pf in patient_findings
        for intervention in pf.interventions.all()
    )
    if has_intervention_label or has_active_intervention:
        return "intervention"

    if patient_findings:
        return "other_findings"

    return None


def _segment_preferred_frame_number(segment: LabelVideoSegment) -> int | None:
    try:
        start_n = int(segment.start_frame_number)
        end_n = int(segment.end_frame_number)
    except (TypeError, ValueError):
        return None

    # Segment ranges are [start, end), so the latest valid frame is end-1.
    if end_n <= start_n:
        return None
    return max(start_n, end_n - 1)


def _report_timestamp(
    report: AnonymExaminationReport,
) -> tuple[datetime | None, str, bool]:
    if report.date:
        return (
            _combine_date_time(report.date, report.time),
            "examination_date",
            True,
        )
    if report.sensitive_meta and report.sensitive_meta.examination_date:
        return (
            _combine_date_time(
                report.sensitive_meta.examination_date,
                report.sensitive_meta.examination_time,
            ),
            "sensitive_meta_examination_date",
            True,
        )
    return None, "missing_examination_timestamp", False


def _pdf_timestamp(pdf: RawPdfFile) -> tuple[datetime | None, str, bool]:
    sm = pdf.sensitive_meta
    if sm and sm.examination_date:
        return (
            _combine_date_time(sm.examination_date, sm.examination_time),
            "examination_date",
            True,
        )
    if getattr(pdf, "date_created", None):
        return _make_aware_if_needed(pdf.date_created), "date_created", False
    return None, "missing_timestamp", False


def _video_timestamp(video: VideoFile) -> tuple[datetime | None, str, bool]:
    sm = video.sensitive_meta
    if sm and sm.examination_date:
        return (
            _combine_date_time(sm.examination_date, sm.examination_time),
            "examination_date",
            True,
        )
    video_date = getattr(video, "date", None)
    if isinstance(video_date, dt_date):
        return _combine_date_time(video_date, None), "video_date", True
    uploaded_at = getattr(video, "uploaded_at", None)
    if isinstance(uploaded_at, datetime):
        return _make_aware_if_needed(uploaded_at), "uploaded_at", False
    date_created = getattr(video, "date_created", None)
    if isinstance(date_created, datetime):
        return _make_aware_if_needed(date_created), "date_created", False
    return None, "missing_timestamp", False


def _resolved_pdf_anonymized_text(pdf: RawPdfFile) -> str | None:
    if isinstance(pdf.anonymized_text, str) and pdf.anonymized_text.strip():
        return pdf.anonymized_text
    full_report = getattr(pdf, "anonym_examination_report", None)
    if full_report is not None and isinstance(full_report.text, str):
        if full_report.text.strip():
            return full_report.text
    sensitive_meta = getattr(pdf, "sensitive_meta", None)
    if sensitive_meta is not None and isinstance(sensitive_meta.anonymized_text, str):
        if sensitive_meta.anonymized_text.strip():
            return sensitive_meta.anonymized_text
    if isinstance(pdf.text, str) and pdf.text.strip():
        return pdf.text
    return None


class PatientMediaTimelineView(APIView):
    """
    Combined media timeline for a patient.

    Endpoint:
        GET /api/media/patients/<patient_id>/timeline/

    Query parameters:
        - patient_examination_id (int, optional):
            Restrict report/pdf/video items to one patient examination.
        - latest_only (bool-ish, optional):
            If true (1/true/yes/on), returns a compact reporting payload:
                {
                  "patient": {...},
                  "latest_report": {...} | null,
                  "latest_video": {...} | null,
                  "latest_frames": [ ... up to 3 ... ]
                }

    latest_only selection behavior:
        - latest_report: newest item among media_type in {"pdf", "full_report"}.
        - latest_video: newest item with media_type == "video".
        - latest_frames:
            Prefer one frame per category (if available) from mapped segments:
              1) polyp
              2) intervention
              3) other_findings
            Remaining slots are filled with newest frame numbers from Frame rows.

    Stream fields for frontend reporting pages:
        - report/video items include `stream_options` with raw + processed URLs.
        - frame entries include `stream_url`.
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request, patient_id: int):
        try:
            patient = Patient.objects.get(pk=patient_id)
        except Patient.DoesNotExist:
            raise Http404(f"Patient with ID {patient_id} not found")

        pe_filter_raw = request.query_params.get("patient_examination_id")
        latest_only = _is_truthy(request.query_params.get("latest_only"))
        pe_filter_id: int | None = None
        if pe_filter_raw not in (None, ""):
            try:
                pe_filter_id = int(pe_filter_raw)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "patient_examination_id must be an integer."},
                    status=400,
                )

        items: list[dict[str, Any]] = []

        reports_qs = AnonymExaminationReport.objects.select_related(
            "patient", "patient_examination", "sensitive_meta", "center", "type"
        ).filter(
            Q(patient_id=patient_id)
            | Q(sensitive_meta__pseudo_patient_id=patient_id)
            | Q(patient_examination__patient_id=patient_id)
        )
        if pe_filter_id is not None:
            reports_qs = reports_qs.filter(patient_examination_id=pe_filter_id)
        reports = reports_qs.distinct().all()
        for report in reports:
            ts, source, is_exam = _report_timestamp(report)
            file_name = None
            try:
                file_name = report.file.name if report.file else None
            except Exception:
                file_name = None
            raw_pdf_id = None
            if hasattr(report, "raw_pdf_file") and report.raw_pdf_file is not None:
                raw_pdf_id = report.raw_pdf_file.pk
            direct_patient = getattr(report, "patient", None)
            pseudo_patient = getattr(
                getattr(report, "sensitive_meta", None), "pseudo_patient", None
            )
            exam_patient = getattr(
                getattr(report, "patient_examination", None), "patient", None
            )
            items.append(
                {
                    "media_type": "full_report",
                    "id": report.pk,
                    "patient_id": patient_id,
                    "timestamp": _safe_iso(ts),
                    "timestamp_source": source,
                    "timestamp_is_examination_date": is_exam,
                    "examination_date": _safe_iso(report.date),
                    "examination_time": _safe_iso(report.time),
                    "center_name": getattr(report.center, "name", None),
                    "file_name": file_name,
                    "raw_pdf_id": raw_pdf_id,
                    "patient_examination_id": report.patient_examination_id,
                    "document_type": getattr(
                        getattr(report, "type", None), "name", None
                    ),
                    "anonymized_text": report.text
                    if isinstance(report.text, str)
                    else None,
                    "linked_patient": _patient_ref(direct_patient),
                    "pseudo_patient": _patient_ref(pseudo_patient),
                    "examination_patient": _patient_ref(exam_patient),
                    "patient_link_sources": [
                        source_name
                        for source_name, value in (
                            ("patient", getattr(report, "patient_id", None)),
                            (
                                "sensitive_meta.pseudo_patient",
                                getattr(
                                    getattr(report, "sensitive_meta", None),
                                    "pseudo_patient_id",
                                    None,
                                ),
                            ),
                            (
                                "patient_examination.patient",
                                getattr(report, "patient_examination_id", None)
                                and getattr(
                                    getattr(report, "patient_examination", None),
                                    "patient_id",
                                    None,
                                ),
                            ),
                        )
                        if value is not None
                    ],
                    "stream_options": (
                        [
                            {
                                "type": "raw",
                                "url": build_absolute_media_url(
                                    request,
                                    build_pdf_stream_path(raw_pdf_id, file_type="raw"),
                                ),
                            },
                            {
                                "type": "processed",
                                "url": build_absolute_media_url(
                                    request,
                                    build_pdf_stream_path(
                                        raw_pdf_id, file_type="processed"
                                    ),
                                ),
                            },
                        ]
                        if raw_pdf_id is not None
                        else []
                    ),
                }
            )

        pdfs_qs = RawPdfFile.objects.select_related(
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
        pdfs = pdfs_qs.distinct().all()
        for pdf in pdfs:
            ts, source, is_exam = _pdf_timestamp(pdf)
            sm = pdf.sensitive_meta
            direct_patient = getattr(pdf, "patient", None)
            pseudo_patient = getattr(sm, "pseudo_patient", None) if sm else None
            full_report = getattr(pdf, "anonym_examination_report", None)
            resolved_anonymized_text = _resolved_pdf_anonymized_text(pdf)
            items.append(
                {
                    "media_type": "pdf",
                    "id": pdf.pk,
                    "uuid": str(pdf.uuid),
                    "patient_id": patient_id,
                    "timestamp": _safe_iso(ts),
                    "timestamp_source": source,
                    "timestamp_is_examination_date": is_exam,
                    "examination_date": _safe_iso(
                        getattr(sm, "examination_date", None)
                    ),
                    "examination_time": _safe_iso(
                        getattr(sm, "examination_time", None)
                    ),
                    "date_created": _safe_iso(getattr(pdf, "date_created", None)),
                    "center_name": getattr(pdf.center, "name", None),
                    "pdf_hash": pdf.pdf_hash,
                    "file_name": getattr(pdf.file, "name", None) if pdf.file else None,
                    "processed_file_name": getattr(pdf.processed_file, "name", None)
                    if pdf.processed_file
                    else None,
                    "full_report_id": getattr(
                        pdf, "anonym_examination_report_id", None
                    ),
                    "document_type": getattr(
                        getattr(full_report, "type", None), "name", None
                    ),
                    "patient_examination_id": getattr(pdf, "examination_id", None)
                    or getattr(full_report, "patient_examination_id", None),
                    "anonymized_text": resolved_anonymized_text,
                    "linked_patient": _patient_ref(direct_patient),
                    "pseudo_patient": _patient_ref(pseudo_patient),
                    "patient_link_sources": [
                        source_name
                        for source_name, value in (
                            ("patient", getattr(pdf, "patient_id", None)),
                            (
                                "sensitive_meta.pseudo_patient",
                                getattr(sm, "pseudo_patient_id", None) if sm else None,
                            ),
                        )
                        if value is not None
                    ],
                    "stream_options": [
                        {
                            "type": "raw",
                            "url": build_absolute_media_url(
                                request,
                                build_pdf_stream_path(pdf.pk, file_type="raw"),
                            ),
                        },
                        {
                            "type": "processed",
                            "url": build_absolute_media_url(
                                request,
                                build_pdf_stream_path(pdf.pk, file_type="processed"),
                            ),
                        },
                    ],
                }
            )

        videos_qs = VideoFile.objects.select_related(
            "patient", "sensitive_meta", "center", "examination", "state"
        ).filter(
            Q(patient_id=patient_id)
            | Q(sensitive_meta__pseudo_patient_id=patient_id)
            | Q(examination__patient_id=patient_id)
        )
        if pe_filter_id is not None:
            videos_qs = videos_qs.filter(examination_id=pe_filter_id)
        videos = videos_qs.distinct().all()
        for video in videos:
            ts, source, is_exam = _video_timestamp(video)
            sm = video.sensitive_meta
            direct_patient = getattr(video, "patient", None)
            pseudo_patient = getattr(sm, "pseudo_patient", None) if sm else None
            exam_patient = getattr(getattr(video, "examination", None), "patient", None)
            active_file_name = None
            try:
                active_file = get_active_video_file(video)
                active_file_name = active_file.name if active_file else None
            except Exception:
                active_file_name = None
            items.append(
                {
                    "media_type": "video",
                    "id": video.pk,
                    "uuid": str(video.uuid),
                    "patient_id": patient_id,
                    "timestamp": _safe_iso(ts),
                    "timestamp_source": source,
                    "timestamp_is_examination_date": is_exam,
                    "examination_date": _safe_iso(
                        getattr(sm, "examination_date", None)
                        or getattr(video, "date", None)
                    ),
                    "examination_time": _safe_iso(
                        getattr(sm, "examination_time", None)
                    ),
                    "uploaded_at": _safe_iso(getattr(video, "uploaded_at", None)),
                    "date_created": _safe_iso(getattr(video, "date_created", None)),
                    "center_name": getattr(video.center, "name", None),
                    "video_hash": video.video_hash,
                    "file_name": active_file_name,
                    "original_file_name": video.original_file_name,
                    "patient_examination_id": video.examination_id,
                    "linked_patient": _patient_ref(direct_patient),
                    "pseudo_patient": _patient_ref(pseudo_patient),
                    "examination_patient": _patient_ref(exam_patient),
                    "patient_link_sources": [
                        source_name
                        for source_name, value in (
                            ("patient", getattr(video, "patient_id", None)),
                            (
                                "sensitive_meta.pseudo_patient",
                                getattr(sm, "pseudo_patient_id", None) if sm else None,
                            ),
                            (
                                "examination.patient",
                                getattr(
                                    getattr(video, "examination", None),
                                    "patient_id",
                                    None,
                                ),
                            ),
                        )
                        if value is not None
                    ],
                    "stream_options": [
                        {
                            "type": "raw",
                            "url": build_absolute_media_url(
                                request,
                                build_video_stream_path(video.pk, file_type="raw"),
                            ),
                        },
                        {
                            "type": "processed",
                            "url": build_absolute_media_url(
                                request,
                                build_video_stream_path(
                                    video.pk, file_type="processed"
                                ),
                            ),
                        },
                    ],
                }
            )

        def _sort_key(item: dict[str, Any]) -> tuple[int, datetime, int]:
            raw_ts = item.get("timestamp")
            parsed_dt: datetime
            if isinstance(raw_ts, str):
                try:
                    parsed_dt = datetime.fromisoformat(raw_ts)
                    if parsed_dt.tzinfo is None:
                        parsed_dt = timezone.make_aware(
                            parsed_dt, timezone.get_current_timezone()
                        )
                    return (1, parsed_dt, int(item.get("id") or 0))
                except Exception:
                    pass
            # Missing timestamps sort last; use id as tie-breaker
            return (0, _make_aware_if_needed(datetime.min), int(item.get("id") or 0))

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

            latest_frames: list[dict[str, Any]] = []
            latest_video_id = (
                latest_video.get("id") if isinstance(latest_video, dict) else None
            )
            if isinstance(latest_video_id, int):
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
                    if category and category not in prioritized_segments:
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
                    latest_frames.append(
                        {
                            "id": None,
                            "video_id": latest_video_id,
                            "frame_number": frame_number,
                            "timestamp": None,
                            "category": category,
                            "selection_source": "segment_priority",
                            "segment_id": selected_segment.pk,
                            "segment_label": getattr(
                                selected_segment.label, "name", None
                            ),
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
                        if frame.frame_number in seen_frame_numbers:
                            continue
                        latest_frames.append(
                            {
                                "id": frame.pk,
                                "video_id": frame.video_id,
                                "frame_number": frame.frame_number,
                                "timestamp": frame.timestamp,
                                "category": "fallback_latest",
                                "selection_source": "latest_frame",
                                "stream_url": build_absolute_media_url(
                                    request,
                                    build_video_frame_stream_path(
                                        frame.video_id, frame.frame_number
                                    ),
                                ),
                            }
                        )
                        seen_frame_numbers.add(frame.frame_number)
                        if len(latest_frames) >= 3:
                            break

            return Response(
                {
                    "patient": {
                        "id": patient.pk,
                        "first_name": patient.first_name,
                        "last_name": patient.last_name,
                        "dob": _safe_iso(patient.dob),
                        "is_real_person": bool(
                            getattr(patient, "is_real_person", True)
                        ),
                        "patient_hash": getattr(patient, "patient_hash", None),
                    },
                    "latest_report": latest_report,
                    "latest_video": latest_video,
                    "latest_frames": latest_frames,
                }
            )

        return Response(
            {
                "patient": {
                    "id": patient.pk,
                    "first_name": patient.first_name,
                    "last_name": patient.last_name,
                    "dob": _safe_iso(patient.dob),
                    "is_real_person": bool(getattr(patient, "is_real_person", True)),
                    "patient_hash": getattr(patient, "patient_hash", None),
                },
                "count": len(items),
                "results": items,
            }
        )
