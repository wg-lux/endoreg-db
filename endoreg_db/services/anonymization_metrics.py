from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from django.contrib.auth.models import AnonymousUser
from django.db.models import (
    Avg,
    Case,
    CharField,
    Count,
    F,
    Max,
    Min,
    Q,
    Sum,
    Subquery,
    Value,
    When,
    Window,
)
from django.db.models.query import QuerySet
from django.db.models.functions import RowNumber
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from lx_dtypes.models.contracts.anonymization_metrics import (
    AnonymizationFieldQualityPayload,
    AnonymizationMetricsFiltersPayload,
    AnonymizationMetricsPayload,
    AnonymizationMetricsQueryBoundsPayload,
    AnonymizationPhiRegionMetricsPayload,
    AnonymizationQualityMetricsPayload,
    AnonymizationWorkflowMetricsPayload,
)
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.label.annotation.frame_box import FrameBoxAnnotation
from endoreg_db.models.media.anonymization_metrics import (
    AnonymizationFieldMetric,
    AnonymizationMetricField,
    AnonymizationValidationMetric,
)
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.state.video import VideoState
from endoreg_db.models.state.anonymization import AnonymizationState

MediaType = Literal["video", "pdf"]
ValidationMetricQuerySet = QuerySet[AnonymizationValidationMetric]
FieldMetricQuerySet = QuerySet[AnonymizationFieldMetric]
VideoFileQuerySet = QuerySet[VideoFile]
RawPdfFileQuerySet = QuerySet[RawPdfFile]
FrameBoxAnnotationQuerySet = QuerySet[FrameBoxAnnotation]
UploadJobQuerySet = QuerySet[UploadJob]
PHI_REGION_LABEL_NAME = "phi_region"
PHI_REGION_INFORMATION_SOURCE_NAME = "lx_anonymizer_phi_detector"
PHI_REGION_ANNOTATOR = "system:lx_anonymizer"
PHI_REGION_IOU_THRESHOLD = 0.3
MAX_METRICS_WINDOW_DAYS = 31
MAX_PHI_REGION_MATCH_ANNOTATIONS = 5000


@dataclass(frozen=True)
class FieldMetricInput:
    field_name: str
    before_value: Any
    after_value: Any
    was_required: bool


REQUIRED_FIELDS_BY_MEDIA_TYPE: dict[MediaType, set[str]] = {
    "video": {
        AnonymizationMetricField.PATIENT_FIRST_NAME,
        AnonymizationMetricField.PATIENT_LAST_NAME,
        AnonymizationMetricField.PATIENT_DOB,
        AnonymizationMetricField.EXAMINATION_DATE,
        AnonymizationMetricField.CASENUMBER,
    },
    "pdf": {
        AnonymizationMetricField.PATIENT_FIRST_NAME,
        AnonymizationMetricField.PATIENT_LAST_NAME,
        AnonymizationMetricField.PATIENT_DOB,
        AnonymizationMetricField.EXAMINATION_DATE,
        AnonymizationMetricField.CASENUMBER,
        AnonymizationMetricField.DOCUMENT_TYPE,
    },
}

EVALUATED_FIELDS: tuple[str, ...] = tuple(
    field.value for field in AnonymizationMetricField
)


@dataclass(frozen=True)
class MetricsFilters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    media_type: MediaType | None = None
    center_id: int | None = None
    document_type: str = ""
    source_system: str = ""


def parse_metrics_filters(query_params: Mapping[str, Any]) -> MetricsFilters:
    date_from = _parse_filter_datetime(query_params.get("date_from"))
    date_to = _parse_filter_datetime(query_params.get("date_to"), end_of_day=True)
    _validate_metrics_window(date_from=date_from, date_to=date_to)

    media_type_raw = str(query_params.get("media_type", "") or "").strip()
    media_type: MediaType | None
    if media_type_raw:
        if media_type_raw not in {"video", "pdf"}:
            raise ValueError("media_type must be either 'video' or 'pdf'.")
        media_type = media_type_raw  # type: ignore[assignment]
    else:
        media_type = None

    center_id_raw = str(query_params.get("center_id", "") or "").strip()
    center_id = None
    if center_id_raw:
        try:
            center_id = int(center_id_raw)
        except ValueError as exc:
            raise ValueError("center_id must be an integer.") from exc

    return MetricsFilters(
        date_from=date_from,
        date_to=date_to,
        media_type=media_type,
        center_id=center_id,
        document_type=str(query_params.get("document_type", "") or "").strip(),
        source_system=str(query_params.get("source_system", "") or "").strip(),
    )


def build_anonymization_metrics_payload(filters: MetricsFilters) -> JsonObject:
    _validate_metrics_window(date_from=filters.date_from, date_to=filters.date_to)
    validation_qs = _filtered_validation_metrics(filters)
    return AnonymizationMetricsPayload(
        schema_version="1.0",
        filters=_serialize_filters(filters),
        query_bounds=AnonymizationMetricsQueryBoundsPayload(
            max_window_days=MAX_METRICS_WINDOW_DAYS,
            max_phi_region_match_annotations=MAX_PHI_REGION_MATCH_ANNOTATIONS,
        ),
        workflow=_workflow_payload(filters=filters, validation_qs=validation_qs),
        field_quality=_field_quality_payload(validation_qs),
        phi_regions=_phi_region_payload(filters),
        quality=_quality_payload(validation_qs),
    ).to_json_object()


def capture_sensitive_meta_metric_values(
    *,
    sensitive_meta: SensitiveMeta | None,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
) -> dict[str, Any]:
    """
    Snapshot comparable values before validation mutates SensitiveMeta.

    The returned dict is used transiently by the caller and must not be persisted
    as-is because it may contain patient-identifying values.
    """

    values: dict[str, Any] = {}
    values[AnonymizationMetricField.PATIENT_FIRST_NAME] = getattr(
        sensitive_meta,
        "patient_first_name",
        None,
    )
    values[AnonymizationMetricField.PATIENT_LAST_NAME] = getattr(
        sensitive_meta,
        "patient_last_name",
        None,
    )
    values[AnonymizationMetricField.PATIENT_DOB] = getattr(
        sensitive_meta,
        "patient_dob",
        None,
    )
    values[AnonymizationMetricField.PATIENT_GENDER] = _gender_name(
        getattr(sensitive_meta, "patient_gender", None)
    )
    values[AnonymizationMetricField.EXAMINATION_DATE] = getattr(
        sensitive_meta,
        "examination_date",
        None,
    )
    values[AnonymizationMetricField.CASENUMBER] = getattr(
        sensitive_meta,
        "casenumber",
        None,
    )
    values[AnonymizationMetricField.CENTER_NAME] = _center_name(
        getattr(sensitive_meta, "center", None) or getattr(media_obj, "center", None)
    )
    values[AnonymizationMetricField.EXTERNAL_ID] = _external_id_value(
        getattr(sensitive_meta, "external_id", None)
    )
    values[AnonymizationMetricField.DOCUMENT_TYPE] = (
        _document_type_value(media_obj) if media_type == "pdf" else None
    )
    return values


def _parse_filter_datetime(value: Any, *, end_of_day: bool = False) -> datetime | None:
    if value in {None, ""}:
        return None
    raw_value = str(value)
    parsed = parse_datetime(raw_value)
    if parsed is None:
        parsed_date = parse_date(raw_value)
        if parsed_date is None:
            raise ValueError(f"Invalid date value: {raw_value}")
        parsed_time = time.max if end_of_day else time.min
        parsed = datetime.combine(parsed_date, parsed_time)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _validate_metrics_window(
    *,
    date_from: datetime | None,
    date_to: datetime | None,
) -> None:
    if date_from is None or date_to is None:
        raise ValueError(
            "date_from and date_to are required and must span no more than "
            f"{MAX_METRICS_WINDOW_DAYS} days."
        )
    if date_to < date_from:
        raise ValueError("date_to must be greater than or equal to date_from.")
    if date_to - date_from > timedelta(days=MAX_METRICS_WINDOW_DAYS):
        raise ValueError(
            "date_from and date_to must span no more than "
            f"{MAX_METRICS_WINDOW_DAYS} days."
        )


def _serialize_filters(filters: MetricsFilters) -> AnonymizationMetricsFiltersPayload:
    if filters.date_from is None or filters.date_to is None:
        raise ValueError("date_from and date_to are required for metrics payloads.")
    return AnonymizationMetricsFiltersPayload(
        date_from=filters.date_from,
        date_to=filters.date_to,
        media_type=filters.media_type,
        center_id=filters.center_id,
        document_type=filters.document_type or None,
        source_system=filters.source_system or None,
    )


def _filtered_validation_metrics(filters: MetricsFilters) -> ValidationMetricQuerySet:
    qs = AnonymizationValidationMetric.objects.all()
    if filters.date_from is not None:
        qs = qs.filter(validated_at__gte=filters.date_from)
    if filters.date_to is not None:
        qs = qs.filter(validated_at__lte=filters.date_to)
    if filters.media_type:
        qs = qs.filter(media_type=filters.media_type)
    if filters.center_id is not None:
        qs = qs.filter(center_id=filters.center_id)
    if filters.document_type:
        qs = qs.filter(document_type=filters.document_type)
    if filters.source_system:
        qs = qs.filter(source_system=filters.source_system)
    return qs


def _workflow_payload(
    *, filters: MetricsFilters, validation_qs: ValidationMetricQuerySet
) -> AnonymizationWorkflowMetricsPayload:
    status_counts = {state.value: 0 for state in AnonymizationState}
    if not filters.media_type or filters.media_type == "video":
        _merge_status_counts(
            status_counts,
            _aggregate_media_status_counts(
                _filtered_video_queryset(filters),
                status_case=VideoState.anonymization_status_case(
                    relation_prefix="state",
                    include_missing_relation=True,
                ),
            ),
        )
    if not filters.media_type or filters.media_type == "pdf":
        _merge_status_counts(
            status_counts,
            _aggregate_media_status_counts(
                _filtered_pdf_queryset(filters),
                status_case=_pdf_status_case(),
            ),
        )

    lost_uploads = _filtered_lost_upload_jobs(filters).count()
    validation_aggregates = cast(
        Mapping[str, object],
        validation_qs.aggregate(
            validation_event_count=Count("id"),
            avg_seconds_to_validation=Avg("seconds_to_validation"),
            min_seconds_to_validation=Min("seconds_to_validation"),
            max_seconds_to_validation=Max("seconds_to_validation"),
        ),
    )
    return AnonymizationWorkflowMetricsPayload(
        status_counts=status_counts,
        pending_validation_count=status_counts.get(
            AnonymizationState.DONE_PROCESSING_ANONYMIZATION.value,
            0,
        ),
        failed_count=status_counts.get(AnonymizationState.FAILED.value, 0),
        lost_count=lost_uploads,
        failed_or_lost_count=status_counts.get(
            AnonymizationState.FAILED.value,
            0,
        )
        + lost_uploads,
        validation_event_count=_int_from_mapping(
            validation_aggregates,
            "validation_event_count",
        ),
        avg_seconds_to_validation=_optional_float_from_mapping(
            validation_aggregates,
            "avg_seconds_to_validation",
        ),
        min_seconds_to_validation=_optional_float_from_mapping(
            validation_aggregates,
            "min_seconds_to_validation",
        ),
        max_seconds_to_validation=_optional_float_from_mapping(
            validation_aggregates,
            "max_seconds_to_validation",
        ),
        median_seconds_to_validation=_database_median_seconds_to_validation(
            validation_qs
        ),
    )


def _filtered_video_queryset(filters: MetricsFilters) -> VideoFileQuerySet:
    qs = VideoFile.objects.select_related("state", "center", "sensitive_meta")
    if filters.center_id is not None:
        qs = qs.filter(center_id=filters.center_id)
    if filters.date_from is not None:
        qs = qs.filter(uploaded_at__gte=filters.date_from)
    if filters.date_to is not None:
        qs = qs.filter(uploaded_at__lte=filters.date_to)
    if filters.source_system:
        qs = qs.filter(_source_media_filter("video", filters.source_system))
    if filters.document_type:
        qs = qs.none()
    return qs


def _filtered_pdf_queryset(filters: MetricsFilters) -> RawPdfFileQuerySet:
    qs = RawPdfFile.objects.select_related(
        "state",
        "center",
        "sensitive_meta",
        "anonym_examination_report__type",
    )
    if filters.center_id is not None:
        qs = qs.filter(center_id=filters.center_id)
    if filters.date_from is not None:
        qs = qs.filter(date_created__gte=filters.date_from)
    if filters.date_to is not None:
        qs = qs.filter(date_created__lte=filters.date_to)
    if filters.document_type:
        qs = qs.filter(
            Q(raw_meta__document_type=filters.document_type)
            | Q(anonym_examination_report__type__name=filters.document_type)
        )
    if filters.source_system:
        qs = qs.filter(_source_media_filter("pdf", filters.source_system))
    return qs


def _source_media_filter(
    media_type: MediaType,
    source_system: str,
    *,
    prefix: str = "",
) -> Q:
    upload_jobs = UploadJob.objects.filter(source_system=source_system)
    sensitive_meta_ids = upload_jobs.exclude(sensitive_meta_id__isnull=True).values(
        "sensitive_meta_id"
    )
    content_hashes = upload_jobs.exclude(content_hash="").values("content_hash")
    hash_field = "video_hash__in" if media_type == "video" else "pdf_hash__in"
    return Q(**{f"{prefix}sensitive_meta_id__in": Subquery(sensitive_meta_ids)}) | Q(
        **{f"{prefix}{hash_field}": Subquery(content_hashes)}
    )


def _aggregate_media_status_counts(
    queryset: VideoFileQuerySet | RawPdfFileQuerySet, *, status_case: Case
) -> dict[str, int]:
    return {
        str(row["anonymization_status"]): int(row["total"] or 0)
        for row in (
            queryset.annotate(anonymization_status=status_case)
            .values("anonymization_status")
            .annotate(total=Count("id"))
        )
    }


def _merge_status_counts(
    status_counts: dict[str, int],
    partial_counts: Mapping[str, int],
) -> None:
    for status_value, count in partial_counts.items():
        status_counts[status_value] = status_counts.get(status_value, 0) + count


def _pdf_status_case() -> Case:
    return Case(
        When(
            state__isnull=True,
            then=Value(AnonymizationState.NOT_STARTED.value),
        ),
        When(
            state__anonymization_validated=True,
            then=Value(AnonymizationState.VALIDATED.value),
        ),
        When(
            state__sensitive_meta_processed=True,
            then=Value(AnonymizationState.DONE_PROCESSING_ANONYMIZATION.value),
        ),
        When(
            state__processing_started=True,
            state__processing_error=False,
            state__anonymized=False,
            then=Value(AnonymizationState.PROCESSING_ANONYMIZING.value),
        ),
        When(
            state__processing_error=True,
            then=Value(AnonymizationState.FAILED.value),
        ),
        When(
            state__processing_started=True,
            then=Value(AnonymizationState.STARTED.value),
        ),
        When(
            state__anonymized=True,
            then=Value(AnonymizationState.ANONYMIZED.value),
        ),
        default=Value(AnonymizationState.NOT_STARTED.value),
        output_field=CharField(),
    )


def _filtered_lost_upload_jobs(filters: MetricsFilters) -> UploadJobQuerySet:
    qs = UploadJob.objects.filter(status=UploadJob.Status.LOST)
    if filters.center_id is not None:
        qs = qs.filter(source_center_id=filters.center_id)
    if filters.date_from is not None:
        qs = qs.filter(created_at__gte=filters.date_from)
    if filters.date_to is not None:
        qs = qs.filter(created_at__lte=filters.date_to)
    if filters.source_system:
        qs = qs.filter(source_system=filters.source_system)
    return qs


def _field_quality_payload(
    validation_qs: ValidationMetricQuerySet,
) -> list[AnonymizationFieldQualityPayload]:
    field_qs: FieldMetricQuerySet = AnonymizationFieldMetric.objects.filter(
        validation_metric__in=validation_qs
    )
    aggregates_by_field = {
        row["field_name"]: row
        for row in field_qs.values("field_name").annotate(
            support=Count("id"),
            changed_count=Count("id", filter=Q(changed=True)),
            exact_match_count=Count("id", filter=Q(exact_match=True)),
            missing_after_validation_count=Count(
                "id",
                filter=Q(was_empty_after_validation=True),
            ),
            mean_similarity=Avg("similarity_score"),
        )
    }
    payload: list[AnonymizationFieldQualityPayload] = []
    for field_name in EVALUATED_FIELDS:
        aggregates = cast(
            Mapping[str, object],
            aggregates_by_field.get(field_name, {}),
        )
        support = _int_from_mapping(aggregates, "support")
        changed_count = _int_from_mapping(aggregates, "changed_count")
        exact_match_count = _int_from_mapping(aggregates, "exact_match_count")
        payload.append(
            AnonymizationFieldQualityPayload(
                field_name=field_name,
                support=support,
                changed_count=changed_count,
                changed_rate=_safe_rate(changed_count, support),
                exact_match_count=exact_match_count,
                exact_match_rate=_safe_rate(exact_match_count, support),
                mean_similarity=_optional_float_from_mapping(
                    aggregates,
                    "mean_similarity",
                ),
                missing_after_validation_count=_int_from_mapping(
                    aggregates,
                    "missing_after_validation_count",
                ),
            )
        )
    return payload


def _quality_payload(
    validation_qs: ValidationMetricQuerySet,
) -> AnonymizationQualityMetricsPayload:
    aggregates = cast(
        Mapping[str, object],
        validation_qs.aggregate(
            evaluated_event_count=Count(
                "id",
                filter=~Q(sensitive_meta_deletion_status=""),
            ),
            residual_phi_detected_count=Count(
                "id",
                filter=Q(residual_phi_detected=True),
            ),
            residual_ocr_match_count=Sum("residual_ocr_match_count"),
            phi_region_false_negative_count=Sum("phi_region_false_negative_count"),
            raw_artifact_residual_count=Sum("raw_artifact_residual_count"),
            missing_sensitive_meta_deletion_count=Sum(
                "missing_sensitive_meta_deletion_count"
            ),
        ),
    )
    status_counts = {
        str(row["sensitive_meta_deletion_status"]): int(row["total"] or 0)
        for row in (
            validation_qs.exclude(sensitive_meta_deletion_status="")
            .values("sensitive_meta_deletion_status")
            .annotate(total=Count("id"))
        )
    }
    policy_counts = {
        str(row["sensitive_meta_policy"]): int(row["total"] or 0)
        for row in (
            validation_qs.exclude(sensitive_meta_policy="")
            .values("sensitive_meta_policy")
            .annotate(total=Count("id"))
        )
    }
    return AnonymizationQualityMetricsPayload(
        evaluated_event_count=_int_from_mapping(aggregates, "evaluated_event_count"),
        residual_phi_detected_count=_int_from_mapping(
            aggregates,
            "residual_phi_detected_count",
        ),
        residual_ocr_match_count=_int_from_mapping(
            aggregates,
            "residual_ocr_match_count",
        ),
        phi_region_false_negative_count=_int_from_mapping(
            aggregates,
            "phi_region_false_negative_count",
        ),
        raw_artifact_residual_count=_int_from_mapping(
            aggregates,
            "raw_artifact_residual_count",
        ),
        missing_sensitive_meta_deletion_count=_int_from_mapping(
            aggregates,
            "missing_sensitive_meta_deletion_count",
        ),
        sensitive_meta_deletion_status_counts=status_counts,
        sensitive_meta_policy_counts=policy_counts,
    )


def _phi_region_payload(
    filters: MetricsFilters,
) -> AnonymizationPhiRegionMetricsPayload:
    if filters.media_type == "pdf":
        return AnonymizationPhiRegionMetricsPayload(
            proposal_count=0,
            human_annotation_count=0,
            matched_count=0,
            precision=None,
            recall=None,
            matching_evaluated=False,
            matching_annotation_count=0,
            max_matching_annotations=MAX_PHI_REGION_MATCH_ANNOTATIONS,
        )

    qs = FrameBoxAnnotation.objects.select_related(
        "frame__video",
        "information_source",
        "label",
    ).filter(label__name=PHI_REGION_LABEL_NAME)
    if filters.center_id is not None:
        qs = qs.filter(frame__video__center_id=filters.center_id)
    if filters.date_from is not None:
        qs = qs.filter(date_created__gte=filters.date_from)
    if filters.date_to is not None:
        qs = qs.filter(date_created__lte=filters.date_to)
    if filters.source_system:
        qs = qs.filter(
            _source_media_filter(
                "video",
                filters.source_system,
                prefix="frame__video__",
            )
        )

    proposal_qs = qs.filter(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    )
    human_qs = qs.exclude(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    )
    proposal_count = proposal_qs.count()
    human_count = human_qs.count()
    matching_annotation_count = proposal_count + human_count
    matching_evaluated = matching_annotation_count <= MAX_PHI_REGION_MATCH_ANNOTATIONS
    matched_count = None
    if matching_evaluated:
        matched_count = (
            _matched_phi_region_count(
                _annotation_box_rows(proposal_qs),
                _annotation_box_rows(human_qs),
            )
            if human_count
            else 0
        )
    return AnonymizationPhiRegionMetricsPayload(
        proposal_count=proposal_count,
        human_annotation_count=human_count,
        matched_count=matched_count,
        precision=(
            _safe_rate(matched_count, proposal_count)
            if human_count and matched_count is not None
            else None
        ),
        recall=(
            _safe_rate(matched_count, human_count)
            if human_count and matched_count is not None
            else None
        ),
        matching_evaluated=matching_evaluated,
        matching_annotation_count=matching_annotation_count,
        max_matching_annotations=MAX_PHI_REGION_MATCH_ANNOTATIONS,
    )


def _annotation_box_rows(
    queryset: FrameBoxAnnotationQuerySet,
) -> list[Mapping[str, object]]:
    return cast(
        list[Mapping[str, object]],
        list(
            queryset.order_by("frame_id", "id").values(
                "id",
                "frame_id",
                "x",
                "y",
                "width",
                "height",
            )[:MAX_PHI_REGION_MATCH_ANNOTATIONS]
        ),
    )


def annotation_box_rows(
    queryset: FrameBoxAnnotationQuerySet,
) -> list[Mapping[str, object]]:
    return _annotation_box_rows(queryset)


def _matched_phi_region_count(
    proposals: Sequence[Mapping[str, Any]],
    human_annotations: Sequence[Mapping[str, Any]],
) -> int:
    humans_by_frame: dict[int, list[Mapping[str, Any]]] = {}
    for human_annotation in human_annotations:
        humans_by_frame.setdefault(int(human_annotation["frame_id"]), []).append(
            human_annotation
        )

    matched_human_ids: set[int] = set()
    matched_count = 0
    for proposal in proposals:
        candidates = humans_by_frame.get(int(proposal["frame_id"]), [])
        best_candidate: Mapping[str, Any] | None = None
        best_iou = 0.0
        for candidate in candidates:
            candidate_id = int(candidate["id"])
            if candidate_id in matched_human_ids:
                continue
            iou = _box_iou(proposal, candidate)
            if iou > best_iou:
                best_candidate = candidate
                best_iou = iou
        if best_candidate is not None and best_iou >= PHI_REGION_IOU_THRESHOLD:
            matched_human_ids.add(int(best_candidate["id"]))
            matched_count += 1
    return matched_count


def matched_phi_region_count(
    proposals: Sequence[Mapping[str, object]],
    human_annotations: Sequence[Mapping[str, object]],
) -> int:
    return _matched_phi_region_count(proposals, human_annotations)


def _box_iou(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    left_x = float(left["x"])
    left_y = float(left["y"])
    left_width = float(left["width"])
    left_height = float(left["height"])
    right_x = float(right["x"])
    right_y = float(right["y"])
    right_width = float(right["width"])
    right_height = float(right["height"])
    left_x2 = left_x + left_width
    left_y2 = left_y + left_height
    right_x2 = right_x + right_width
    right_y2 = right_y + right_height
    inter_x1 = max(left_x, right_x)
    inter_y1 = max(left_y, right_y)
    inter_x2 = min(left_x2, right_x2)
    inter_y2 = min(left_y2, right_y2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    intersection = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
    left_area = left_width * left_height
    right_area = right_width * right_height
    union = left_area + right_area - intersection
    return float(intersection / union) if union > 0 else 0.0


def _safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator / denominator)


def _int_from_mapping(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        return int(value)
    raise TypeError(f"{key} must be numeric.")


def _optional_float_from_mapping(
    payload: Mapping[str, object],
    key: str,
) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float | str):
        return float(value)
    raise TypeError(f"{key} must be numeric.")


def _database_median_seconds_to_validation(
    validation_qs: ValidationMetricQuerySet,
) -> float | None:
    seconds_qs = validation_qs.exclude(seconds_to_validation__isnull=True)
    seconds_count = int(seconds_qs.count())
    if seconds_count == 0:
        return None
    lower_position = (seconds_count + 1) // 2
    upper_position = (seconds_count + 2) // 2
    median_values = cast(
        list[float],
        list(
            seconds_qs.annotate(
                row_number=Window(
                    expression=RowNumber(),
                    order_by=F("seconds_to_validation").asc(),
                )
            )
            .filter(row_number__in=[lower_position, upper_position])
            .order_by("row_number")
            .values_list("seconds_to_validation", flat=True)[:2]
        ),
    )
    if not median_values:
        return None
    return float(sum(median_values) / len(median_values))


def record_validation_metrics(
    *,
    request: Any,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
    payload: Mapping[str, Any],
    before_values: Mapping[str, Any],
    status_before: str | None,
    status_after: str | None,
) -> AnonymizationValidationMetric:
    """
    Persist derived-only metrics for one successful validation event.

    Raw before/after values are compared only in memory and discarded before the
    database write.
    """

    sensitive_meta = getattr(media_obj, "sensitive_meta", None)
    validated_at = timezone.now()
    source_system, anonymizer_version = _resolve_provenance(
        media_obj=media_obj,
        sensitive_meta=sensitive_meta,
        media_type=media_type,
    )
    field_inputs = list(
        _field_metric_inputs(
            media_type=media_type,
            before_values=before_values,
            payload=payload,
        )
    )
    field_rows = [_derive_field_metric(field_input) for field_input in field_inputs]
    total_fields = len(field_rows)
    changed_fields = sum(1 for row in field_rows if row["changed"])
    exact_match_fields = sum(1 for row in field_rows if row["exact_match"])
    missing_after_validation_fields = sum(
        1 for row in field_rows if row["was_empty_after_validation"]
    )
    similarity_values = [
        row["similarity_score"]
        for row in field_rows
        if row["similarity_score"] is not None
    ]
    mean_similarity = (
        sum(similarity_values) / len(similarity_values) if similarity_values else None
    )
    if media_type == "video":
        video_media = cast(VideoFile, media_obj)
        pdf_media = None
    else:
        pdf_media = cast(RawPdfFile, media_obj)
        video_media = None

    validation_metric = AnonymizationValidationMetric.objects.create(
        media_type=media_type,
        video=video_media,
        pdf=pdf_media,
        sensitive_meta=sensitive_meta,
        center=getattr(media_obj, "center", None)
        or getattr(sensitive_meta, "center", None),
        validator_user=_authenticated_user_or_none(getattr(request, "user", None)),
        validator_username=_username(getattr(request, "user", None)),
        validated_at=validated_at,
        status_before=status_before or "",
        status_after=status_after or "",
        document_type=str(payload.get("document_type") or ""),
        source_system=source_system,
        anonymizer_source="lx_anonymizer",
        anonymizer_version=anonymizer_version,
        no_more_names_confirmed=_optional_bool(payload.get("no_more_names_confirmed")),
        seconds_to_validation=_seconds_to_validation(
            media_obj=media_obj,
            media_type=media_type,
            validated_at=validated_at,
        ),
        total_fields=total_fields,
        changed_fields=changed_fields,
        exact_match_fields=exact_match_fields,
        missing_after_validation_fields=missing_after_validation_fields,
        mean_similarity=mean_similarity,
    )
    AnonymizationFieldMetric.objects.bulk_create(
        [
            AnonymizationFieldMetric(
                validation_metric=validation_metric,
                field_name=row["field_name"],
                present_before=row["present_before"],
                present_after=row["present_after"],
                changed=row["changed"],
                exact_match=row["exact_match"],
                similarity_score=row["similarity_score"],
                was_required=row["was_required"],
                was_empty_after_validation=row["was_empty_after_validation"],
            )
            for row in field_rows
        ]
    )
    return validation_metric


def _field_metric_inputs(
    *,
    media_type: MediaType,
    before_values: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> Iterable[FieldMetricInput]:
    required_fields = REQUIRED_FIELDS_BY_MEDIA_TYPE[media_type]
    for field_name in EVALUATED_FIELDS:
        yield FieldMetricInput(
            field_name=field_name,
            before_value=before_values.get(field_name),
            after_value=payload.get(field_name),
            was_required=field_name in required_fields,
        )


def _derive_field_metric(field_input: FieldMetricInput) -> dict[str, Any]:
    before_normalized = _normalize_comparable_value(field_input.before_value)
    after_normalized = _normalize_comparable_value(field_input.after_value)
    present_before = bool(before_normalized)
    present_after = bool(after_normalized)
    exact_match = before_normalized == after_normalized
    return {
        "field_name": field_input.field_name,
        "present_before": present_before,
        "present_after": present_after,
        "changed": not exact_match,
        "exact_match": exact_match,
        "similarity_score": _similarity(before_normalized, after_normalized),
        "was_required": field_input.was_required,
        "was_empty_after_validation": field_input.was_required and not present_after,
    }


def _normalize_comparable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    normalized = " ".join(str(value).strip().split())
    if normalized.casefold() in {"", "none", "null", "unknown", "undefined", "-"}:
        return ""
    return normalized.casefold()


def _similarity(before_value: str, after_value: str) -> float:
    if not before_value and not after_value:
        return 1.0
    if not before_value or not after_value:
        return 0.0
    if before_value == after_value:
        return 1.0
    return float(SequenceMatcher(None, before_value, after_value).ratio())


def _gender_name(gender_obj: Any) -> str | None:
    if gender_obj is None:
        return None
    for attr in ("name", "value"):
        value = getattr(gender_obj, attr, None)
        if value:
            return str(value)
    return str(gender_obj)


def _center_name(center_obj: Any) -> str | None:
    if center_obj is None:
        return None
    value = getattr(center_obj, "name", None)
    return str(value) if value else None


def _external_id_value(external_id_obj: Any) -> str | None:
    if external_id_obj is None:
        return None
    value = getattr(external_id_obj, "external_id", None)
    return str(value) if value else None


def _document_type_value(media_obj: VideoFile | RawPdfFile) -> str | None:
    report = getattr(media_obj, "anonym_examination_report", None)
    report_type = getattr(report, "type", None) if report is not None else None
    report_type_name = getattr(report_type, "name", None)
    if report_type_name:
        return str(report_type_name)
    raw_meta = getattr(media_obj, "raw_meta", None)
    if isinstance(raw_meta, Mapping):
        raw_meta_payload = cast(Mapping[str, object], raw_meta)
        document_type = raw_meta_payload.get("document_type")
        if document_type:
            return str(document_type)
    return None


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _authenticated_user_or_none(user: Any) -> Any | None:
    if user is None or isinstance(user, AnonymousUser):
        return None
    if getattr(user, "is_authenticated", False):
        return user
    return None


def _username(user: Any) -> str:
    if user is None or isinstance(user, AnonymousUser):
        return ""
    username = getattr(user, "get_username", None)
    if callable(username):
        return str(username())
    return str(getattr(user, "username", "") or "")


def _seconds_to_validation(
    *,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
    validated_at: datetime,
) -> float | None:
    created_at = (
        getattr(media_obj, "uploaded_at", None)
        if media_type == "video"
        else getattr(media_obj, "date_created", None)
    )
    if created_at is None:
        return None
    if timezone.is_naive(created_at):
        created_at = timezone.make_aware(created_at, timezone.get_current_timezone())
    delta = validated_at - created_at
    return max(delta.total_seconds(), 0.0)


def _resolve_provenance(
    *,
    media_obj: VideoFile | RawPdfFile,
    sensitive_meta: SensitiveMeta | None,
    media_type: MediaType,
) -> tuple[str, str]:
    upload_job = _find_upload_job(
        media_obj=media_obj,
        sensitive_meta=sensitive_meta,
        media_type=media_type,
    )
    source_system = getattr(upload_job, "source_system", "") if upload_job else ""
    version = _provenance_version_from_media(media_obj)
    if not version and upload_job is not None:
        provenance = getattr(upload_job, "processing_provenance", None)
        if isinstance(provenance, Mapping):
            version = _version_from_provenance(cast(Mapping[str, object], provenance))
    if not version:
        try:
            version = importlib.metadata.version("lx-anonymizer")
        except importlib.metadata.PackageNotFoundError:
            version = ""
    return source_system or "", version or ""


def _find_upload_job(
    *,
    media_obj: VideoFile | RawPdfFile,
    sensitive_meta: SensitiveMeta | None,
    media_type: MediaType,
) -> UploadJob | None:
    filters = Q()
    if sensitive_meta is not None and sensitive_meta.pk:
        filters |= Q(sensitive_meta_id=sensitive_meta.pk)
    content_hash = (
        getattr(media_obj, "video_hash", None)
        if media_type == "video"
        else getattr(media_obj, "pdf_hash", None)
    )
    if content_hash:
        filters |= Q(content_hash=content_hash)
    if not filters:
        return None
    return (
        UploadJob.objects.filter(filters).order_by("-updated_at", "-created_at").first()
    )


def _provenance_version_from_media(media_obj: VideoFile | RawPdfFile) -> str:
    raw_meta = getattr(media_obj, "raw_meta", None)
    if isinstance(raw_meta, Mapping):
        return _version_from_provenance(cast(Mapping[str, object], raw_meta))
    meta = getattr(media_obj, "meta", None)
    if isinstance(meta, Mapping):
        return _version_from_provenance(cast(Mapping[str, object], meta))
    return ""


def _version_from_provenance(payload: Mapping[str, object]) -> str:
    candidates: tuple[object, object, Mapping[str, object]] = (
        payload.get("anonymizer_provenance"),
        payload.get("lx_anonymizer_provenance"),
        payload,
    )
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        provenance_candidate = cast(Mapping[str, object], candidate)
        version = provenance_candidate.get("anonymizer_version")
        if version:
            return str(version)
    return ""
