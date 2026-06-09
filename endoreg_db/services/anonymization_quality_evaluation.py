from __future__ import annotations

import logging
import re
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from pydantic import BaseModel, Field

from endoreg_db.import_files.file_storage.cleanup import staging_cleanup_roots
from endoreg_db.models.label.annotation.frame_box import FrameBoxAnnotation
from endoreg_db.models.media.anonymization_metrics import AnonymizationValidationMetric
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.anonymization_metrics import (
    MAX_PHI_REGION_MATCH_ANNOTATIONS,
    PHI_REGION_ANNOTATOR,
    PHI_REGION_INFORMATION_SOURCE_NAME,
    PHI_REGION_LABEL_NAME,
    _annotation_box_rows,
    _matched_phi_region_count,
)
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.storage import file_exists

logger = logging.getLogger(__name__)

MediaType = Literal["video", "pdf"]


class SensitiveMetaHandlingPolicy(StrEnum):
    RETAIN_FOR_GOVERNANCE = "retain_for_governance"
    CLEAR_DIRECT_IDENTIFIERS = "clear_direct_identifiers"
    DELETE_SENSITIVE_META = "delete_sensitive_meta"


class QualityEvaluationStatus(StrEnum):
    PASSED = "passed"
    RESIDUAL_PHI_DETECTED = "residual_phi_detected"
    NOT_VALIDATED = "not_validated"
    FAILED_OR_LOST = "failed_or_lost"
    NO_SENSITIVE_META = "no_sensitive_meta"
    NOT_MEASURABLE = "not_measurable"


DIRECT_IDENTIFIER_FIELDS: tuple[str, ...] = (
    "patient_first_name",
    "patient_last_name",
    "patient_dob",
    "examination_date",
    "examination_time",
    "casenumber",
    "examiner_first_name",
    "examiner_last_name",
    "file_path",
    "text",
    "anonymized_text",
    "endoscope_sn",
    "external_id",
    "validation_comment",
)


class AnonymizationQualityResult(BaseModel):
    media_type: MediaType
    media_id: int
    status: str
    residual_phi_detected: bool
    checked_fields: list[str] = Field(default_factory=list)
    leaked_field_count: int = 0
    missing_sensitive_meta_deletion_count: int = 0
    raw_artifact_residual_count: int = 0
    processed_artifact_sha256: str = ""
    warnings: list[str] = Field(default_factory=list)


class AnonymizationQualitySummary(BaseModel):
    total: int
    residual_phi_detected_count: int
    leaked_field_count: int
    missing_sensitive_meta_deletion_count: int
    raw_artifact_residual_count: int
    status_counts: dict[str, int]


class AnonymizationQualityPayload(BaseModel):
    schema_version: str = "1.0"
    sensitive_meta_policy: SensitiveMetaHandlingPolicy
    policy_applied: bool
    summary: AnonymizationQualitySummary
    results: list[AnonymizationQualityResult]


def evaluate_anonymization_quality(
    *,
    media_type: Literal["all", "video", "pdf"] = "all",
    video_ids: Iterable[int] = (),
    pdf_ids: Iterable[int] = (),
    center_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 0,
    include_unvalidated: bool = False,
    sensitive_meta_policy: SensitiveMetaHandlingPolicy = (
        SensitiveMetaHandlingPolicy.CLEAR_DIRECT_IDENTIFIERS
    ),
    apply_policy: bool = False,
    allow_sensitive_meta_delete: bool = False,
) -> AnonymizationQualityPayload:
    results: list[AnonymizationQualityResult] = []
    if media_type in {"all", "video"}:
        for video in _video_queryset(
            video_ids=tuple(video_ids),
            center_id=center_id,
            date_from=date_from,
            date_to=date_to,
            include_unvalidated=include_unvalidated,
            limit=limit,
        ):
            results.append(
                evaluate_media_object(
                    media_obj=video,
                    media_type="video",
                    sensitive_meta_policy=sensitive_meta_policy,
                    apply_policy=apply_policy,
                    allow_sensitive_meta_delete=allow_sensitive_meta_delete,
                )
            )
            if limit and len(results) >= limit:
                break

    if (not limit or len(results) < limit) and media_type in {"all", "pdf"}:
        remaining_limit = limit - len(results) if limit else 0
        for pdf in _pdf_queryset(
            pdf_ids=tuple(pdf_ids),
            center_id=center_id,
            date_from=date_from,
            date_to=date_to,
            include_unvalidated=include_unvalidated,
            limit=remaining_limit,
        ):
            results.append(
                evaluate_media_object(
                    media_obj=pdf,
                    media_type="pdf",
                    sensitive_meta_policy=sensitive_meta_policy,
                    apply_policy=apply_policy,
                    allow_sensitive_meta_delete=allow_sensitive_meta_delete,
                )
            )

    return AnonymizationQualityPayload(
        sensitive_meta_policy=sensitive_meta_policy,
        policy_applied=apply_policy,
        summary=_quality_summary(results),
        results=results,
    )


def evaluate_media_object(
    *,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
    sensitive_meta_policy: SensitiveMetaHandlingPolicy,
    apply_policy: bool,
    allow_sensitive_meta_delete: bool = False,
) -> AnonymizationQualityResult:
    warnings: list[str] = []
    media_id = int(media_obj.pk or 0)

    if _media_failed_or_lost(media_obj):
        result = AnonymizationQualityResult(
            media_type=media_type,
            media_id=media_id,
            status=QualityEvaluationStatus.FAILED_OR_LOST.value,
            residual_phi_detected=False,
            warnings=["media_failed_or_lost"],
        )
        _persist_quality_metrics(
            media_obj=media_obj,
            media_type=media_type,
            result=result,
            policy=sensitive_meta_policy,
            sensitive_meta_deletion_status="not_applied_failed_or_lost",
            phi_region_false_negative_count=0,
        )
        return result

    if not _media_is_validated(media_obj):
        result = AnonymizationQualityResult(
            media_type=media_type,
            media_id=media_id,
            status=QualityEvaluationStatus.NOT_VALIDATED.value,
            residual_phi_detected=False,
            warnings=["human_validation_not_complete"],
        )
        _persist_quality_metrics(
            media_obj=media_obj,
            media_type=media_type,
            result=result,
            policy=sensitive_meta_policy,
            sensitive_meta_deletion_status="not_applied_not_validated",
            phi_region_false_negative_count=0,
        )
        return result

    sensitive_meta = getattr(media_obj, "sensitive_meta", None)
    if not isinstance(sensitive_meta, SensitiveMeta):
        result = AnonymizationQualityResult(
            media_type=media_type,
            media_id=media_id,
            status=QualityEvaluationStatus.NO_SENSITIVE_META.value,
            residual_phi_detected=False,
            processed_artifact_sha256=_processed_artifact_sha256(media_obj),
            raw_artifact_residual_count=_raw_artifact_residual_count(media_obj),
            warnings=["sensitive_meta_missing"],
        )
        _persist_quality_metrics(
            media_obj=media_obj,
            media_type=media_type,
            result=result,
            policy=sensitive_meta_policy,
            sensitive_meta_deletion_status="missing",
            phi_region_false_negative_count=0,
        )
        return result

    identifier_values = _identifier_values(sensitive_meta, media_obj=media_obj)
    checked_fields = sorted(identifier_values)
    residual_corpus = _residual_text_corpus(media_obj, sensitive_meta=sensitive_meta)
    if not residual_corpus:
        warnings.append("residual_ocr_not_measurable")
    leaked_fields = _matched_identifier_fields(identifier_values, residual_corpus)
    phi_region_false_negative_count = (
        _phi_region_false_negative_count(media_obj) if media_type == "video" else 0
    )
    if media_type == "video" and phi_region_false_negative_count == 0:
        warnings.extend(_phi_region_measurement_warnings(media_obj))

    processed_sha256 = _processed_artifact_sha256(media_obj)
    if not processed_sha256:
        warnings.append("processed_artifact_hash_not_available")

    raw_artifact_residual_count = _raw_artifact_residual_count(media_obj)
    deletion_status, missing_sensitive_meta_deletion_count = (
        _apply_or_audit_sensitive_meta_policy(
            media_obj=media_obj,
            media_type=media_type,
            sensitive_meta=sensitive_meta,
            policy=sensitive_meta_policy,
            apply_policy=apply_policy,
            allow_sensitive_meta_delete=allow_sensitive_meta_delete,
        )
    )
    if sensitive_meta.patient_hash or sensitive_meta.examination_hash:
        warnings.append("pseudonym_hashes_retained_as_controlled_linkage")

    residual_phi_detected = bool(leaked_fields) or phi_region_false_negative_count > 0
    if residual_phi_detected:
        status = QualityEvaluationStatus.RESIDUAL_PHI_DETECTED.value
    elif not processed_sha256:
        status = QualityEvaluationStatus.NOT_MEASURABLE.value
    elif not residual_corpus and media_type == "video":
        status = QualityEvaluationStatus.NOT_MEASURABLE.value
    else:
        status = QualityEvaluationStatus.PASSED.value

    result = AnonymizationQualityResult(
        media_type=media_type,
        media_id=media_id,
        status=status,
        residual_phi_detected=residual_phi_detected,
        checked_fields=checked_fields,
        leaked_field_count=len(leaked_fields),
        missing_sensitive_meta_deletion_count=missing_sensitive_meta_deletion_count,
        raw_artifact_residual_count=raw_artifact_residual_count,
        processed_artifact_sha256=processed_sha256,
        warnings=sorted(set(warnings)),
    )
    _persist_quality_metrics(
        media_obj=media_obj,
        media_type=media_type,
        result=result,
        policy=sensitive_meta_policy,
        sensitive_meta_deletion_status=deletion_status,
        phi_region_false_negative_count=phi_region_false_negative_count,
    )
    return result


def parse_quality_datetime(
    value: str | None, *, end_of_day: bool = False
) -> datetime | None:
    if value in {None, ""}:
        return None
    assert value is not None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date is None:
            raise ValueError(f"Invalid date value: {value}")
        parsed = datetime.combine(
            parsed_date,
            datetime.max.time() if end_of_day else datetime.min.time(),
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _video_queryset(
    *,
    video_ids: tuple[int, ...],
    center_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    include_unvalidated: bool,
    limit: int,
):
    qs = VideoFile.objects.select_related("state", "sensitive_meta", "center")
    if video_ids:
        qs = qs.filter(pk__in=video_ids)
    elif not include_unvalidated:
        qs = qs.filter(state__anonymization_validated=True)
    if center_id is not None:
        qs = qs.filter(center_id=center_id)
    if date_from is not None:
        qs = qs.filter(uploaded_at__gte=date_from)
    if date_to is not None:
        qs = qs.filter(uploaded_at__lte=date_to)
    qs = qs.order_by("pk")
    return qs[:limit] if limit else qs


def _pdf_queryset(
    *,
    pdf_ids: tuple[int, ...],
    center_id: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
    include_unvalidated: bool,
    limit: int,
):
    qs = RawPdfFile.objects.select_related("state", "sensitive_meta", "center")
    if pdf_ids:
        qs = qs.filter(pk__in=pdf_ids)
    elif not include_unvalidated:
        qs = qs.filter(state__anonymization_validated=True)
    if center_id is not None:
        qs = qs.filter(center_id=center_id)
    if date_from is not None:
        qs = qs.filter(date_created__gte=date_from)
    if date_to is not None:
        qs = qs.filter(date_created__lte=date_to)
    qs = qs.order_by("pk")
    return qs[:limit] if limit else qs


def _media_failed_or_lost(media_obj: VideoFile | RawPdfFile) -> bool:
    state = getattr(media_obj, "state", None)
    if bool(getattr(state, "processing_error", False)):
        return True
    meta = getattr(media_obj, "meta", None)
    return isinstance(meta, Mapping) and meta.get("integrity_status") == "lost"


def _media_is_validated(media_obj: VideoFile | RawPdfFile) -> bool:
    return bool(
        getattr(getattr(media_obj, "state", None), "anonymization_validated", False)
    )


def _quality_summary(
    results: list[AnonymizationQualityResult],
) -> AnonymizationQualitySummary:
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
    return AnonymizationQualitySummary(
        total=len(results),
        residual_phi_detected_count=sum(
            1 for result in results if result.residual_phi_detected
        ),
        leaked_field_count=sum(result.leaked_field_count for result in results),
        missing_sensitive_meta_deletion_count=sum(
            result.missing_sensitive_meta_deletion_count for result in results
        ),
        raw_artifact_residual_count=sum(
            result.raw_artifact_residual_count for result in results
        ),
        status_counts=status_counts,
    )


def _identifier_values(
    sensitive_meta: SensitiveMeta,
    *,
    media_obj: VideoFile | RawPdfFile,
) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {}
    _append_identifier(
        candidates, "patient_first_name", sensitive_meta.patient_first_name
    )
    _append_identifier(
        candidates, "patient_last_name", sensitive_meta.patient_last_name
    )
    _append_date_identifiers(candidates, "patient_dob", sensitive_meta.patient_dob)
    _append_date_identifiers(
        candidates,
        "examination_date",
        sensitive_meta.examination_date,
    )
    _append_identifier(candidates, "casenumber", sensitive_meta.casenumber)
    _append_identifier(
        candidates,
        "examiner_first_name",
        sensitive_meta.examiner_first_name,
    )
    _append_identifier(
        candidates,
        "examiner_last_name",
        sensitive_meta.examiner_last_name,
    )
    _append_identifier(
        candidates, "center_name", _center_name(media_obj, sensitive_meta)
    )
    external_id = getattr(sensitive_meta.external_id, "external_id", None)
    _append_identifier(candidates, "external_id", external_id)
    return {key: values for key, values in candidates.items() if values}


def _append_identifier(
    candidates: dict[str, list[str]],
    field_name: str,
    value: Any,
) -> None:
    if value in {None, ""}:
        return
    normalized = " ".join(str(value).strip().split())
    if len(normalized) < 3:
        return
    if normalized.casefold() in {"none", "null", "unknown", "undefined"}:
        return
    candidates.setdefault(field_name, [])
    if normalized not in candidates[field_name]:
        candidates[field_name].append(normalized)


def _append_date_identifiers(
    candidates: dict[str, list[str]],
    field_name: str,
    value: Any,
) -> None:
    if value in {None, ""}:
        return
    if isinstance(value, datetime):
        date_value = value.date()
    elif isinstance(value, date):
        date_value = value
    else:
        parsed = parse_date(str(value))
        if parsed is None:
            return
        date_value = parsed
    _append_identifier(candidates, field_name, date_value.isoformat())
    _append_identifier(candidates, field_name, date_value.strftime("%d.%m.%Y"))


def _center_name(
    media_obj: VideoFile | RawPdfFile, sensitive_meta: SensitiveMeta
) -> str:
    center = getattr(sensitive_meta, "center", None) or getattr(
        media_obj, "center", None
    )
    return str(getattr(center, "name", "") or "")


def _residual_text_corpus(
    media_obj: VideoFile | RawPdfFile,
    *,
    sensitive_meta: SensitiveMeta,
) -> str:
    values: list[str] = []
    anonymized_text = getattr(sensitive_meta, "anonymized_text", None)
    if anonymized_text:
        values.append(str(anonymized_text))
    if isinstance(media_obj, RawPdfFile):
        report_text = getattr(media_obj, "anonymized_text", None)
        if report_text:
            values.append(str(report_text))
    else:
        meta = getattr(media_obj, "meta", None)
        if isinstance(meta, Mapping):
            for key in ("anonymized_text", "processed_text", "residual_ocr_text"):
                value = meta.get(key)
                if value:
                    values.append(str(value))
    return "\n".join(values)


def _matched_identifier_fields(
    identifier_values: Mapping[str, list[str]],
    corpus: str,
) -> set[str]:
    if not corpus:
        return set()
    haystack = _normalize_for_matching(corpus)
    matched: set[str] = set()
    for field_name, values in identifier_values.items():
        for value in values:
            if _contains_identifier(haystack, value):
                matched.add(field_name)
                break
    return matched


def _normalize_for_matching(value: str) -> str:
    return " ".join(value.casefold().split())


def _contains_identifier(normalized_haystack: str, raw_needle: str) -> bool:
    needle = _normalize_for_matching(raw_needle)
    if not needle:
        return False
    if all(char.isalnum() or char.isspace() for char in needle):
        pattern = rf"(?<!\w){re.escape(needle)}(?!\w)"
        return re.search(pattern, normalized_haystack) is not None
    return needle in normalized_haystack


def _phi_region_false_negative_count(media_obj: VideoFile | RawPdfFile) -> int:
    if not isinstance(media_obj, VideoFile):
        return 0
    qs = FrameBoxAnnotation.objects.select_related(
        "information_source",
        "label",
        "frame",
    ).filter(frame__video=media_obj, label__name=PHI_REGION_LABEL_NAME)
    proposal_qs = qs.filter(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    )
    human_qs = qs.exclude(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    )
    human_count = human_qs.count()
    total_count = proposal_qs.count() + human_count
    if not human_count or total_count > MAX_PHI_REGION_MATCH_ANNOTATIONS:
        return 0
    matched_count = _matched_phi_region_count(
        _annotation_box_rows(proposal_qs),
        _annotation_box_rows(human_qs),
    )
    return max(human_count - matched_count, 0)


def _phi_region_measurement_warnings(media_obj: VideoFile | RawPdfFile) -> list[str]:
    if not isinstance(media_obj, VideoFile):
        return []
    qs = FrameBoxAnnotation.objects.filter(
        frame__video=media_obj,
        label__name=PHI_REGION_LABEL_NAME,
    )
    proposal_count = qs.filter(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    ).count()
    human_count = qs.exclude(
        information_source__name=PHI_REGION_INFORMATION_SOURCE_NAME,
        annotator=PHI_REGION_ANNOTATOR,
    ).count()
    if not human_count:
        return ["phi_region_human_annotations_missing"]
    if proposal_count + human_count > MAX_PHI_REGION_MATCH_ANNOTATIONS:
        return ["phi_region_matching_skipped_annotation_limit"]
    return []


def _processed_artifact_sha256(media_obj: VideoFile | RawPdfFile) -> str:
    processed_file = getattr(media_obj, "processed_file", None)
    if not processed_file or not getattr(processed_file, "name", None):
        return ""
    if not file_exists(processed_file):
        return ""
    try:
        return sha256_file(processed_file)
    except Exception:
        logger.warning(
            "Failed to hash processed artifact for %s:%s",
            media_obj.__class__.__name__,
            getattr(media_obj, "pk", None),
            exc_info=True,
        )
        return ""


def _raw_artifact_residual_count(media_obj: VideoFile | RawPdfFile) -> int:
    count = 0
    raw_file = getattr(media_obj, "raw_file", None) or getattr(media_obj, "file", None)
    if raw_file and getattr(raw_file, "name", None) and file_exists(raw_file):
        count += 1
    raw_stream_path = None
    get_raw_stream_path = getattr(media_obj, "get_raw_stream_path", None)
    if callable(get_raw_stream_path):
        raw_stream_path = get_raw_stream_path()
    if isinstance(raw_stream_path, Path) and raw_stream_path.exists():
        count += 1
    content_hash = str(
        getattr(media_obj, "video_hash", "") or getattr(media_obj, "pdf_hash", "") or ""
    )
    if content_hash:
        count += _staging_residual_count(content_hash)
    return count


def _staging_residual_count(content_hash: str) -> int:
    count = 0
    for root in staging_cleanup_roots():
        if not root.exists():
            continue
        try:
            for candidate in root.rglob(f"*{content_hash}*"):
                if candidate.is_file() and not candidate.is_symlink():
                    count += 1
        except OSError:
            logger.warning(
                "Failed to scan staging root for anonymization quality audit."
            )
    return count


def _apply_or_audit_sensitive_meta_policy(
    *,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
    sensitive_meta: SensitiveMeta,
    policy: SensitiveMetaHandlingPolicy,
    apply_policy: bool,
    allow_sensitive_meta_delete: bool,
) -> tuple[str, int]:
    if policy == SensitiveMetaHandlingPolicy.RETAIN_FOR_GOVERNANCE:
        if apply_policy:
            _mark_sensitive_meta_policy(
                sensitive_meta,
                policy=policy,
                status="retained_by_policy",
            )
        return "retained_by_policy", 0

    if policy == SensitiveMetaHandlingPolicy.DELETE_SENSITIVE_META:
        if not apply_policy or not allow_sensitive_meta_delete:
            return "delete_not_applied_requires_explicit_confirmation", (
                _direct_identifier_residual_count(sensitive_meta)
            )
        if _sensitive_meta_has_other_references(
            media_obj=media_obj,
            media_type=media_type,
            sensitive_meta=sensitive_meta,
        ):
            return "delete_skipped_referenced", _direct_identifier_residual_count(
                sensitive_meta
            )
        with transaction.atomic():
            if media_type == "video":
                VideoFile.objects.filter(pk=media_obj.pk).update(sensitive_meta=None)
            else:
                RawPdfFile.objects.filter(pk=media_obj.pk).update(sensitive_meta=None)
            sensitive_meta.delete()
        return "deleted", 0

    if not apply_policy:
        missing_count = _direct_identifier_residual_count(sensitive_meta)
        return ("pending_clear" if missing_count else "cleared"), missing_count

    with transaction.atomic():
        locked_meta = SensitiveMeta.objects.select_for_update().get(
            pk=sensitive_meta.pk
        )
        _clear_sensitive_meta_direct_identifiers(locked_meta, policy=policy)
        locked_meta.refresh_from_db()
        return "cleared", _direct_identifier_residual_count(locked_meta)


def _mark_sensitive_meta_policy(
    sensitive_meta: SensitiveMeta,
    *,
    policy: SensitiveMetaHandlingPolicy,
    status: str,
) -> None:
    SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(
        direct_identifier_policy=policy.value,
        direct_identifier_tombstone={
            "schema_version": "1.0",
            "policy": policy.value,
            "status": status,
            "direct_values_retained": True,
        },
    )


def _clear_sensitive_meta_direct_identifiers(
    sensitive_meta: SensitiveMeta,
    *,
    policy: SensitiveMetaHandlingPolicy,
) -> None:
    updates: dict[str, Any] = {}
    cleared_fields: list[str] = []
    for field_name in DIRECT_IDENTIFIER_FIELDS:
        if not hasattr(sensitive_meta, field_name):
            continue
        current_value = getattr(sensitive_meta, field_name)
        if not _direct_identifier_present(current_value):
            continue
        updates[field_name] = _cleared_value_for_field(field_name)
        cleared_fields.append(field_name)

    cleared_examiners = False
    if sensitive_meta.pk and sensitive_meta.examiners.exists():
        sensitive_meta.examiners.clear()
        cleared_examiners = True

    cleared_at = timezone.now()
    updates.update(
        {
            "direct_identifiers_cleared_at": cleared_at,
            "direct_identifier_policy": policy.value,
            "direct_identifier_tombstone": {
                "schema_version": "1.0",
                "policy": policy.value,
                "cleared_at": cleared_at.isoformat(),
                "cleared_fields_count": len(cleared_fields),
                "cleared_examiners": cleared_examiners,
                "pseudonym_hashes_retained": bool(
                    sensitive_meta.patient_hash or sensitive_meta.examination_hash
                ),
            },
        }
    )
    if updates:
        SensitiveMeta.objects.filter(pk=sensitive_meta.pk).update(**updates)


def _cleared_value_for_field(field_name: str) -> Any:
    if field_name == "validation_comment":
        return ""
    return None


def _direct_identifier_residual_count(sensitive_meta: SensitiveMeta) -> int:
    count = 0
    for field_name in DIRECT_IDENTIFIER_FIELDS:
        if hasattr(sensitive_meta, field_name) and _direct_identifier_present(
            getattr(sensitive_meta, field_name)
        ):
            count += 1
    if sensitive_meta.pk and sensitive_meta.examiners.exists():
        count += 1
    return count


def _direct_identifier_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip()
        return bool(normalized) and normalized.casefold() not in {
            "unknown",
            "none",
            "null",
            "undefined",
        }
    return True


def _sensitive_meta_has_other_references(
    *,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
    sensitive_meta: SensitiveMeta,
) -> bool:
    video_qs = VideoFile.objects.filter(sensitive_meta=sensitive_meta)
    pdf_qs = RawPdfFile.objects.filter(sensitive_meta=sensitive_meta)
    if media_type == "video":
        video_qs = video_qs.exclude(pk=media_obj.pk)
    else:
        pdf_qs = pdf_qs.exclude(pk=media_obj.pk)
    return video_qs.exists() or pdf_qs.exists()


def _persist_quality_metrics(
    *,
    media_obj: VideoFile | RawPdfFile,
    media_type: MediaType,
    result: AnonymizationQualityResult,
    policy: SensitiveMetaHandlingPolicy,
    sensitive_meta_deletion_status: str,
    phi_region_false_negative_count: int,
) -> None:
    metric_qs = AnonymizationValidationMetric.objects.filter(
        Q(video=media_obj) if media_type == "video" else Q(pdf=media_obj)
    ).order_by("-validated_at", "-pk")
    metric = metric_qs.first()
    if metric is None:
        logger.info(
            "No validation metric exists for anonymization quality result %s:%s.",
            media_type,
            result.media_id,
        )
        return
    metric.residual_ocr_match_count = result.leaked_field_count
    metric.phi_region_false_negative_count = phi_region_false_negative_count
    metric.raw_artifact_residual_count = result.raw_artifact_residual_count
    metric.missing_sensitive_meta_deletion_count = (
        result.missing_sensitive_meta_deletion_count
    )
    metric.residual_phi_detected = result.residual_phi_detected
    metric.sensitive_meta_policy = policy.value
    metric.sensitive_meta_deletion_status = sensitive_meta_deletion_status
    metric.save(
        update_fields=[
            "residual_ocr_match_count",
            "phi_region_false_negative_count",
            "raw_artifact_residual_count",
            "missing_sensitive_meta_deletion_count",
            "residual_phi_detected",
            "sensitive_meta_policy",
            "sensitive_meta_deletion_status",
        ]
    )
