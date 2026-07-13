from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict, cast

from django.db.models import Exists, F, OuterRef, Q, QuerySet, Subquery
from django.utils.dateparse import parse_date

from endoreg_db.models.label.annotation.image_classification import (
    ImageClassificationAnnotation,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.medical.patient.patient_examination import PatientExamination
from endoreg_db.models.medical.patient.patient_finding import PatientFinding
from endoreg_db.utils.media_urls import (
    build_absolute_media_url,
    build_pdf_stream_path,
    build_video_hls_playlist_path,
)


MAX_COHORT_PREVIEW_ROWS = 500
DEFAULT_COHORT_PREVIEW_ROWS = 100


class StudyMediaRow(TypedDict):
    id: int
    stream_url: str
    availability: str


class StudyReportRow(StudyMediaRow):
    document_type: str


class StudyCaseRow(TypedDict):
    patient_examination_id: int
    case_hash: str
    patient_hash: str
    examination_name: str
    examination_date: str | None
    center_keys: list[str]
    findings: list[str]
    annotation_labels: list[str]
    reports: list[StudyReportRow]
    videos: list[StudyMediaRow]


class StudySummary(TypedDict):
    case_count: int
    patient_count: int
    report_count: int
    video_count: int


class StudyCenterOption(TypedDict):
    key: str
    label: str


class StudyOptions(TypedDict):
    centers: list[StudyCenterOption]
    examinations: list[str]
    document_types: list[str]
    findings: list[str]
    annotation_labels: list[str]


class StudyFiltersPayload(TypedDict):
    date_from: str | None
    date_to: str | None
    center_key: str
    examination_name: str
    document_type: str
    finding: str
    annotation_label: str
    has_report: bool | None
    has_video: bool | None
    limit: int


class StudyCohortPayload(TypedDict):
    schema_version: str
    filters: StudyFiltersPayload
    summary: StudySummary
    cases: list[StudyCaseRow]
    options: StudyOptions


@dataclass(frozen=True)
class StudyCohortFilters:
    date_from: date | None = None
    date_to: date | None = None
    center_key: str = ""
    examination_name: str = ""
    document_type: str = ""
    finding: str = ""
    annotation_label: str = ""
    has_report: bool | None = None
    has_video: bool | None = None
    limit: int = DEFAULT_COHORT_PREVIEW_ROWS


def _query_text(query_params: Mapping[str, Any], key: str) -> str:
    return str(query_params.get(key, "") or "").strip()


def _parse_optional_date(query_params: Mapping[str, Any], key: str) -> date | None:
    raw_value = _query_text(query_params, key)
    if not raw_value:
        return None
    parsed = parse_date(raw_value)
    if parsed is None:
        raise ValueError(f"{key} must use YYYY-MM-DD format.")
    return parsed


def _parse_optional_bool(query_params: Mapping[str, Any], key: str) -> bool | None:
    raw_value = _query_text(query_params, key).lower()
    if not raw_value:
        return None
    if raw_value in {"1", "true", "yes"}:
        return True
    if raw_value in {"0", "false", "no"}:
        return False
    raise ValueError(f"{key} must be true or false.")


def _parse_limit(query_params: Mapping[str, Any]) -> int:
    raw_value = _query_text(query_params, "limit")
    if not raw_value:
        return DEFAULT_COHORT_PREVIEW_ROWS
    try:
        limit = int(raw_value)
    except ValueError as exc:
        raise ValueError("limit must be an integer.") from exc
    if limit < 1 or limit > MAX_COHORT_PREVIEW_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_COHORT_PREVIEW_ROWS}.")
    return limit


def parse_study_cohort_filters(
    query_params: Mapping[str, Any],
) -> StudyCohortFilters:
    date_from = _parse_optional_date(query_params, "date_from")
    date_to = _parse_optional_date(query_params, "date_to")
    if date_from is not None and date_to is not None and date_to < date_from:
        raise ValueError("date_to must be greater than or equal to date_from.")

    return StudyCohortFilters(
        date_from=date_from,
        date_to=date_to,
        center_key=_query_text(query_params, "center_key"),
        examination_name=_query_text(query_params, "examination_name"),
        document_type=_query_text(query_params, "document_type"),
        finding=_query_text(query_params, "finding"),
        annotation_label=_query_text(query_params, "annotation_label"),
        has_report=_parse_optional_bool(query_params, "has_report"),
        has_video=_parse_optional_bool(query_params, "has_video"),
        limit=_parse_limit(query_params),
    )


def _eligible_report_queryset() -> QuerySet[RawPdfFile]:
    return (
        RawPdfFile.objects.select_related(
            "center",
            "state",
            "pdf_type",
            "anonym_examination_report__type",
        )
        .filter(
            state__anonymization_validated=True,
            state__processed_file_sha256__gt="",
        )
        .exclude(processed_file="")
    )


def _eligible_video_queryset() -> QuerySet[VideoFile]:
    return (
        VideoFile.objects.select_related("center", "state")
        .filter(
            state__anonymization_validated=True,
            state__processed_file_sha256__gt="",
        )
        .exclude(processed_file="")
    )


def _report_case_filter(case_ids: QuerySet[PatientExamination]) -> Q:
    case_id_subquery = cast(Any, Subquery(case_ids.values("pk")))
    return Q(examination_id__in=case_id_subquery) | Q(
        anonym_examination_report__patient_examination_id__in=case_id_subquery
    )


def _video_case_filter(case_ids: QuerySet[PatientExamination]) -> Q:
    case_id_subquery = cast(Any, Subquery(case_ids.values("pk")))
    return Q(examination_id__in=case_id_subquery) | Q(
        patient_examination__id__in=case_id_subquery
    )


def _base_case_queryset() -> QuerySet[PatientExamination]:
    eligible_reports = _eligible_report_queryset().filter(
        Q(examination_id=OuterRef("pk"))
        | Q(anonym_examination_report__patient_examination_id=OuterRef("pk"))
    )
    eligible_videos = _eligible_video_queryset().filter(
        Q(examination_id=OuterRef("pk")) | Q(patient_examination__id=OuterRef("pk"))
    )
    return (
        PatientExamination.objects.select_related("patient", "examination")
        .filter(
            patient__is_real_person=False,
            patient__patient_hash__isnull=False,
        )
        .exclude(patient__patient_hash="")
        .annotate(
            cohort_has_report=Exists(eligible_reports),
            cohort_has_video=Exists(eligible_videos),
        )
        .filter(Q(cohort_has_report=True) | Q(cohort_has_video=True))
    )


def _apply_filters(
    queryset: QuerySet[PatientExamination],
    filters: StudyCohortFilters,
) -> QuerySet[PatientExamination]:
    if filters.date_from is not None:
        queryset = queryset.filter(date_start__gte=filters.date_from)
    if filters.date_to is not None:
        queryset = queryset.filter(date_start__lte=filters.date_to)
    if filters.examination_name:
        queryset = queryset.filter(examination__name__iexact=filters.examination_name)
    if filters.finding:
        queryset = queryset.filter(
            patient_findings__is_active=True,
            patient_findings__finding__name__iexact=filters.finding,
        )
    if filters.has_report is not None:
        queryset = queryset.filter(cohort_has_report=filters.has_report)
    if filters.has_video is not None:
        queryset = queryset.filter(cohort_has_video=filters.has_video)

    if filters.center_key:
        matching_report = _eligible_report_queryset().filter(
            Q(examination_id=OuterRef("pk"))
            | Q(anonym_examination_report__patient_examination_id=OuterRef("pk")),
            center__center_key=filters.center_key,
        )
        matching_video = _eligible_video_queryset().filter(
            Q(examination_id=OuterRef("pk"))
            | Q(patient_examination__id=OuterRef("pk")),
            center__center_key=filters.center_key,
        )
        queryset = queryset.annotate(
            cohort_center_report=Exists(matching_report),
            cohort_center_video=Exists(matching_video),
        ).filter(Q(cohort_center_report=True) | Q(cohort_center_video=True))

    if filters.document_type:
        matching_report = _eligible_report_queryset().filter(
            Q(examination_id=OuterRef("pk"))
            | Q(anonym_examination_report__patient_examination_id=OuterRef("pk")),
            Q(pdf_type__name__iexact=filters.document_type)
            | Q(anonym_examination_report__type__name__iexact=filters.document_type)
            | Q(raw_meta__document_type__iexact=filters.document_type),
        )
        queryset = queryset.annotate(
            cohort_document_type=Exists(matching_report)
        ).filter(cohort_document_type=True)

    if filters.annotation_label:
        matching_annotation = (
            ImageClassificationAnnotation.objects.filter(
                value=True,
                label__name__iexact=filters.annotation_label,
                frame__video__state__anonymization_validated=True,
                frame__video__state__processed_file_sha256__gt="",
            )
            .exclude(frame__video__processed_file="")
            .filter(
                Q(frame__video__examination_id=OuterRef("pk"))
                | Q(frame__video__patient_examination__id=OuterRef("pk"))
            )
        )
        queryset = queryset.annotate(
            cohort_annotation_label=Exists(matching_annotation)
        ).filter(cohort_annotation_label=True)

    return queryset.distinct()


def _field_file_available(field_file: object | None) -> bool:
    name = str(getattr(field_file, "name", "") or "").strip()
    storage = getattr(field_file, "storage", None)
    if not name or storage is None:
        return False
    try:
        if not bool(storage.exists(name)):
            return False
        size = getattr(field_file, "size", 0)
        return int(size or 0) > 0
    except (OSError, ValueError, TypeError):
        return False


def _report_case_id(report: RawPdfFile) -> int | None:
    if report.examination_id is not None:
        return report.examination_id
    full_report = report.anonym_examination_report
    if full_report is None:
        return None
    return full_report.patient_examination_id


def _video_case_id(video: VideoFile) -> int | None:
    if video.examination_id is not None:
        return video.examination_id
    patient_examination = getattr(video, "patient_examination", None)
    return cast(int | None, getattr(patient_examination, "pk", None))


def _report_document_type(report: RawPdfFile) -> str:
    full_report = report.anonym_examination_report
    full_report_type = getattr(full_report, "type", None)
    full_report_type_name = str(getattr(full_report_type, "name", "") or "").strip()
    if full_report_type_name:
        return full_report_type_name

    pdf_type_name = str(getattr(report.pdf_type, "name", "") or "").strip()
    if pdf_type_name:
        return pdf_type_name

    raw_meta = report.raw_meta
    if isinstance(raw_meta, dict):
        raw_value = raw_meta.get("document_type")
        if isinstance(raw_value, str):
            return raw_value.strip()
    return ""


def _center_option(center: object | None) -> StudyCenterOption | None:
    if center is None:
        return None
    key = str(getattr(center, "center_key", "") or "").strip()
    if not key:
        return None
    label = str(
        getattr(center, "display_name", "") or getattr(center, "name", "") or key
    ).strip()
    return {"key": key, "label": label}


def _serialize_filters(filters: StudyCohortFilters) -> StudyFiltersPayload:
    return {
        "date_from": filters.date_from.isoformat() if filters.date_from else None,
        "date_to": filters.date_to.isoformat() if filters.date_to else None,
        "center_key": filters.center_key,
        "examination_name": filters.examination_name,
        "document_type": filters.document_type,
        "finding": filters.finding,
        "annotation_label": filters.annotation_label,
        "has_report": filters.has_report,
        "has_video": filters.has_video,
        "limit": filters.limit,
    }


def build_study_cohort_payload(
    filters: StudyCohortFilters,
    *,
    request: Any | None = None,
) -> StudyCohortPayload:
    filtered_cases = _apply_filters(_base_case_queryset(), filters)
    case_count = filtered_cases.count()
    patient_count = filtered_cases.values("patient_id").distinct().count()

    reports_for_scope = _eligible_report_queryset().filter(
        _report_case_filter(filtered_cases)
    )
    videos_for_scope = _eligible_video_queryset().filter(
        _video_case_filter(filtered_cases)
    )
    report_count = reports_for_scope.order_by().values("pk").distinct().count()
    video_count = videos_for_scope.order_by().values("pk").distinct().count()

    preview_cases = list(
        filtered_cases.order_by(F("date_start").desc(nulls_last=True), "pk")[
            : filters.limit
        ]
    )
    preview_case_ids = [case.pk for case in preview_cases]

    reports_by_case: dict[int, list[StudyReportRow]] = {}
    videos_by_case: dict[int, list[StudyMediaRow]] = {}
    center_options: dict[str, StudyCenterOption] = {}
    center_keys_by_case: dict[int, set[str]] = {}
    document_types: set[str] = set()
    video_ids: list[int] = []

    if preview_case_ids:
        preview_case_qs = PatientExamination.objects.filter(pk__in=preview_case_ids)
        for report in _eligible_report_queryset().filter(
            _report_case_filter(preview_case_qs)
        ):
            case_id = _report_case_id(report)
            if case_id is None or not _field_file_available(report.processed_file):
                continue
            document_type = _report_document_type(report)
            if document_type:
                document_types.add(document_type)
            center = _center_option(report.center)
            if center is not None:
                center_options[center["key"]] = center
                center_keys_by_case.setdefault(case_id, set()).add(center["key"])
            reports_by_case.setdefault(case_id, []).append(
                {
                    "id": report.pk,
                    "document_type": document_type,
                    "stream_url": build_absolute_media_url(
                        request,
                        build_pdf_stream_path(report.pk, file_type="processed"),
                    ),
                    "availability": "local",
                }
            )

        for video in _eligible_video_queryset().filter(
            _video_case_filter(preview_case_qs)
        ):
            case_id = _video_case_id(video)
            if case_id is None or not _field_file_available(video.processed_file):
                continue
            video_ids.append(video.pk)
            center = _center_option(video.center)
            if center is not None:
                center_options[center["key"]] = center
                center_keys_by_case.setdefault(case_id, set()).add(center["key"])
            videos_by_case.setdefault(case_id, []).append(
                {
                    "id": video.pk,
                    "stream_url": build_absolute_media_url(
                        request,
                        build_video_hls_playlist_path(video.pk, file_type="processed"),
                    ),
                    "availability": "local",
                }
            )

    findings_by_case: dict[int, set[str]] = {}
    for case_id, finding_name in PatientFinding.objects.filter(
        patient_examination_id__in=preview_case_ids,
        is_active=True,
    ).values_list("patient_examination_id", "finding__name"):
        findings_by_case.setdefault(case_id, set()).add(str(finding_name))

    annotations_by_video: dict[int, set[str]] = {}
    for video_id, label_name in (
        ImageClassificationAnnotation.objects.filter(
            frame__video_id__in=video_ids,
            value=True,
        )
        .values_list("frame__video_id", "label__name")
        .distinct()
    ):
        annotations_by_video.setdefault(video_id, set()).add(str(label_name))

    cases: list[StudyCaseRow] = []
    for patient_examination in preview_cases:
        case_id = patient_examination.pk
        report_rows = sorted(
            reports_by_case.get(case_id, []), key=lambda row: row["id"]
        )
        video_rows = sorted(videos_by_case.get(case_id, []), key=lambda row: row["id"])
        if not report_rows and not video_rows:
            continue

        annotation_labels: set[str] = set()
        for video_row in video_rows:
            annotation_labels.update(annotations_by_video.get(video_row["id"], set()))

        patient_hash = str(patient_examination.patient.patient_hash or "").strip()
        examination_name = str(
            getattr(patient_examination.examination, "name", "") or ""
        ).strip()
        cases.append(
            {
                "patient_examination_id": case_id,
                "case_hash": patient_examination.hash,
                "patient_hash": patient_hash,
                "examination_name": examination_name,
                "examination_date": (
                    patient_examination.date_start.isoformat()
                    if patient_examination.date_start
                    else None
                ),
                "center_keys": sorted(center_keys_by_case.get(case_id, set())),
                "findings": sorted(findings_by_case.get(case_id, set())),
                "annotation_labels": sorted(annotation_labels),
                "reports": report_rows,
                "videos": video_rows,
            }
        )

    scope_case_ids = cast(Any, Subquery(filtered_cases.values("pk")))
    finding_options = list(
        PatientFinding.objects.filter(
            patient_examination_id__in=scope_case_ids,
            is_active=True,
        )
        .order_by("finding__name")
        .values_list("finding__name", flat=True)
        .distinct()
    )
    examination_options = list(
        filtered_cases.exclude(examination__name__isnull=True)
        .exclude(examination__name="")
        .order_by("examination__name")
        .values_list("examination__name", flat=True)
        .distinct()
    )
    annotation_options = list(
        ImageClassificationAnnotation.objects.filter(
            value=True,
            frame__video__state__anonymization_validated=True,
            frame__video__state__processed_file_sha256__gt="",
        )
        .exclude(frame__video__processed_file="")
        .filter(
            Q(frame__video__examination_id__in=scope_case_ids)
            | Q(frame__video__patient_examination__id__in=scope_case_ids)
        )
        .order_by("label__name")
        .values_list("label__name", flat=True)
        .distinct()
    )

    # Include taxonomy values from the complete filtered scope, not only the preview page.
    for report in reports_for_scope:
        document_type = _report_document_type(report)
        if document_type:
            document_types.add(document_type)
        center = _center_option(report.center)
        if center is not None:
            center_options[center["key"]] = center
    for video in videos_for_scope:
        center = _center_option(video.center)
        if center is not None:
            center_options[center["key"]] = center

    return {
        "schema_version": "1.0",
        "filters": _serialize_filters(filters),
        "summary": {
            "case_count": case_count,
            "patient_count": patient_count,
            "report_count": report_count,
            "video_count": video_count,
        },
        "cases": cases,
        "options": {
            "centers": sorted(center_options.values(), key=lambda item: item["label"]),
            "examinations": [str(value) for value in examination_options],
            "document_types": sorted(document_types),
            "findings": [str(value) for value in finding_options],
            "annotation_labels": [str(value) for value in annotation_options],
        },
    }
