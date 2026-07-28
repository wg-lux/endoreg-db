import logging
from datetime import date as dt_date, datetime, time as dt_time
from typing import Any, Protocol, TypedDict, cast

from django.contrib.auth.models import AnonymousUser
from django.db import models, transaction
from django.utils import timezone
from endoreg_db.services.raw_pdf_files.metadata import ReportMetaJsonObject
from lx_dtypes.models.contracts import DocumentType as DocumentTypeContract
from lx_dtypes.models.contracts.video_text_metadata import VideoTextMetaPayload
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework.views import APIView

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.models.other.tag import Tag
from endoreg_db.serializers.anonymization import (
    SensitiveMetaValidateSerializer,
)
from endoreg_db.services.anonymization_metrics import (
    capture_sensitive_meta_metric_values,
    record_validation_metrics,
)
from endoreg_db.services.auto_case_resolution import AutoCaseResolutionResult
from endoreg_db.services.report_materialization import (
    DOCUMENT_TYPE_VALUES,
    build_report_context_from_validation,
    ensure_document_types,
    upsert_anonym_examination_report_from_pdf,
)
from endoreg_db.services.raw_pdf_files import validate_report_metadata_annotation
from endoreg_db.services.video_files import (
    get_or_create_video_state,
)
from endoreg_db.services.validated_identity import commit_validated_media_identity
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.operation_log import (
    record_operation,
    ACTION_ANONYMIZATION_VALIDATED,
    STATUS_PROCESSING,
    STATUS_ANONYMIZED,
)


logger = logging.getLogger(__name__)


class _TagRelation(Protocol):
    def set(
        self,
        objs: object,
        *,
        clear: bool = False,
        through_defaults: object | None = None,
    ) -> None: ...


class _ValidationState(Protocol):
    def refresh_from_db(self) -> None: ...

    def mark_dob_verified(self) -> None: ...

    def mark_names_verified(self) -> None: ...

    def mark_anonymized(self) -> None: ...

    def save(self, *args: object, **kwargs: object) -> None: ...


class _ValidatedSensitiveMeta(Protocol):
    pk: int | None
    center_id: int | None
    center: "_CenterLike | None"
    validation_comment: str
    tags: "_TagRelation"
    pseudo_patient_id: object
    pseudo_examination_id: object
    patient_hash: str | None
    examination_hash: str | None
    state: _ValidationState | None

    def save(self, *args: object, **kwargs: object) -> None: ...

    def get_or_create_state(self) -> None: ...

    def create_anonymized_record(self) -> None: ...


class _CenterLike(Protocol):
    name: str


class _VideoValidationLike(Protocol):
    meta: object
    state: _ValidationState | None
    sensitive_meta: SensitiveMeta | None
    center: _CenterLike | None
    state_id: int | None
    anonymization_status: str | None

    def save(self, *args: object, **kwargs: object) -> None: ...

    def validate_metadata_annotation(self, payload: VideoTextMetaPayload) -> bool: ...


class _PdfValidationLike(Protocol):
    raw_meta: dict[str, Any] | None
    sensitive_meta: SensitiveMeta | None
    center: _CenterLike | None
    center_id: int | None
    anonymized_text: str | None
    examination_id: int | None
    anonym_examination_report_id: int | None
    state: _ValidationState | None

    def save(self, *args: object, **kwargs: object) -> None: ...

    def validate_metadata_annotation(self, payload: ReportMetaJsonObject) -> bool: ...


class ValidationOperationMetaPayload(TypedDict):
    timestamp: str
    timestamp_source: str
    examination_date: str | None


class VideoValidationPayload(dict[str, object]):
    def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
        return dict(self)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def anonymization_document_types_dropdown(_request: Request):
    ensure_document_types()
    return Response(
        [{"value": value, "label": value} for value in DOCUMENT_TYPE_VALUES],
        status=status.HTTP_200_OK,
    )


def _state_status_value(state_obj: object) -> str | None:
    """Return anonymization status as string if present, else None."""
    if state_obj is None:
        return None
    st = getattr(state_obj, "anonymization_status", None)
    if st is None:
        return None
    return str(getattr(st, "value", st))


def _request_actor(request: Request) -> models.Model | None:
    user = request.user
    if isinstance(user, AnonymousUser):
        return None
    return cast(models.Model, user)


def _preferred_validation_timestamp(payload: ReportMetaJsonObject) -> tuple[str, str]:
    """
    Prefer a manually supplied examination_date as the validation timestamp.

    Returns:
        (iso_timestamp, source)
    """
    exam_date = payload.get("examination_date")
    if isinstance(exam_date, datetime):
        if timezone.is_naive(exam_date):
            exam_date = timezone.make_aware(exam_date, timezone.get_current_timezone())
        return exam_date.isoformat(), "manual_examination_date"
    if isinstance(exam_date, dt_date):
        preferred_dt = timezone.make_aware(
            datetime.combine(exam_date, dt_time.min), timezone.get_current_timezone()
        )
        return preferred_dt.isoformat(), "manual_examination_date"

    now_iso = timezone.now().isoformat()
    return now_iso, "request_time"


def _validation_operation_meta(
    payload: ReportMetaJsonObject,
) -> ValidationOperationMetaPayload:
    timestamp, source = _preferred_validation_timestamp(payload)
    meta: ValidationOperationMetaPayload = {
        "timestamp": timestamp,
        "timestamp_source": source,
        "examination_date": None,
    }
    exam_date = payload.get("examination_date")
    if isinstance(exam_date, (dt_date, datetime)):
        if isinstance(exam_date, datetime):
            meta["examination_date"] = exam_date.date().isoformat()
        else:
            meta["examination_date"] = exam_date.isoformat()
    return meta


def _video_text_meta_payload_data(
    payload: ReportMetaJsonObject,
) -> ReportMetaJsonObject:
    normalized: ReportMetaJsonObject = dict(payload)
    for field_name in ("patient_dob", "examination_date"):
        value = normalized.get(field_name)
        if isinstance(value, datetime):
            normalized[field_name] = value.date().isoformat()
        elif isinstance(value, dt_date):
            normalized[field_name] = value.isoformat()
    return normalized


def _prepare_validation_payload(
    base_payload: ReportMetaJsonObject,
    file_obj: object,
) -> ReportMetaJsonObject:
    """Return a fresh payload tailored for the given media object."""
    prepared: ReportMetaJsonObject = dict(base_payload)
    prepared.pop("file_type", None)
    prepared.pop("no_more_names_confirmed", None)
    _apply_payload_center_name(prepared=prepared, file_obj=file_obj)
    _apply_payload_gender(prepared=prepared, base_payload=base_payload)
    return prepared


def _apply_payload_center_name(
    *,
    prepared: ReportMetaJsonObject,
    file_obj: object,
) -> None:
    center = getattr(file_obj, "center", None)
    center_name = getattr(center, "name", None)
    if center_name and not prepared.get("center_name"):
        prepared["center_name"] = center_name


def _apply_payload_gender(
    *,
    prepared: ReportMetaJsonObject,
    base_payload: ReportMetaJsonObject,
) -> None:
    raw_gender = base_payload.get("patient_gender")
    if raw_gender is None:
        return
    gender = str(raw_gender).strip().lower()
    if not gender:
        return
    if gender in {"m", "male", "männlich"}:
        prepared["patient_gender"] = "male"
    elif gender in {"w", "f", "female", "weiblich"}:
        prepared["patient_gender"] = "female"
    else:
        logger.warning(
            "Unsupported patient_gender value %r; leaving value unchanged",
            raw_gender,
        )
        prepared.pop("patient_gender", None)


def _prepare_video_validation_payload(
    base_payload: ReportMetaJsonObject,
    file_obj: object,
) -> VideoValidationPayload:
    prepared = _prepare_validation_payload(base_payload, file_obj)
    validated_payload = VideoTextMetaPayload.model_validate(
        _video_text_meta_payload_data(prepared)
    )
    return VideoValidationPayload(validated_payload.model_dump(mode="python"))


def _persist_pdf_validation_state(
    *,
    pdf: RawPdfFile,
    payload: ReportMetaJsonObject,
    validated_at_iso: str,
    document_type: DocumentTypeContract,
) -> str:
    pdf_obj = cast(_PdfValidationLike, pdf)
    original_anonymized_text = _initialize_pdf_anonymized_text(
        pdf_obj=pdf_obj,
        payload=payload,
    )

    report_context = build_report_context_from_validation(
        pdf=pdf,
        payload=payload,
        document_type_name=document_type,
    )
    resolved_text = report_context.anonymized_text
    raw_meta = _pdf_raw_meta_payload(pdf_obj)
    sensitive_meta = pdf_obj.sensitive_meta
    raw_meta.update(
        {
            "document_type": report_context.document_type.value,
            "validation_source": "anonymization_validate",
            "validated_at": validated_at_iso,
            "patient_hash": report_context.patient_hash,
            "examination_hash": report_context.examination_hash,
            "pseudo_patient_id": report_context.patient_id,
            "pseudo_examination_id": report_context.patient_examination_id,
        }
    )
    update_fields = _pdf_validation_update_fields(
        pdf=pdf,
        pdf_obj=pdf_obj,
        sensitive_meta=sensitive_meta,
        raw_meta=raw_meta,
        resolved_text=resolved_text,
        original_anonymized_text=original_anonymized_text,
    )
    if update_fields:
        pdf_obj.save(update_fields=update_fields)
    return resolved_text


def _initialize_pdf_anonymized_text(
    *,
    pdf_obj: _PdfValidationLike,
    payload: ReportMetaJsonObject,
) -> str | None:
    original_text = pdf_obj.anonymized_text
    if original_text is None:
        payload_text = payload.get("anonymized_text")
        pdf_obj.anonymized_text = payload_text if isinstance(payload_text, str) else ""
    return original_text


def _pdf_raw_meta_payload(pdf_obj: _PdfValidationLike) -> ReportMetaJsonObject:
    if isinstance(pdf_obj.raw_meta, dict):
        return cast(ReportMetaJsonObject, dict(pdf_obj.raw_meta))
    return cast(ReportMetaJsonObject, {})


def _assign_pdf_center_from_sensitive_meta(
    *,
    pdf_obj: _PdfValidationLike,
    sensitive_meta: SensitiveMeta | None,
) -> bool:
    if getattr(pdf_obj, "center_id", None) is not None or sensitive_meta is None:
        return False
    if not getattr(sensitive_meta, "center_id", None):
        return False
    pdf_obj.center = cast(_CenterLike, getattr(sensitive_meta, "center"))
    return True


def _pdf_validation_update_fields(
    *,
    pdf: RawPdfFile,
    pdf_obj: _PdfValidationLike,
    sensitive_meta: SensitiveMeta | None,
    raw_meta: ReportMetaJsonObject,
    resolved_text: str,
    original_anonymized_text: str | None,
) -> list[str]:
    update_fields: list[str] = []
    if original_anonymized_text != resolved_text:
        pdf.anonymized_text = resolved_text
        update_fields.append("anonymized_text")
    if _assign_pdf_center_from_sensitive_meta(
        pdf_obj=pdf_obj,
        sensitive_meta=sensitive_meta,
    ):
        update_fields.append("center")
    if pdf_obj.raw_meta != raw_meta:
        pdf_obj.raw_meta = raw_meta
        update_fields.append("raw_meta")
    return update_fields


def _build_pdf_validation_context(pdf: RawPdfFile) -> dict[str, object] | None:
    sensitive_meta = cast(_PdfValidationLike, pdf).sensitive_meta
    if sensitive_meta is None:
        return None
    validated_meta = cast(_ValidatedSensitiveMeta, sensitive_meta)

    return {
        "sensitive_meta_id": validated_meta.pk,
        "patient_hash_display": (
            f"...{validated_meta.patient_hash[-8:]}"
            if validated_meta.patient_hash
            else None
        ),
        "examination_hash_display": (
            f"...{validated_meta.examination_hash[-8:]}"
            if validated_meta.examination_hash
            else None
        ),
        "pseudo_patient_id": validated_meta.pseudo_patient_id,
        "pseudo_examination_id": validated_meta.pseudo_examination_id,
    }


def _normalize_tag_names(raw_tags: object) -> list[str]:
    if not isinstance(raw_tags, list):
        return []

    normalized_tags: list[str] = []
    seen: set[str] = set()
    for entry in cast(list[object], raw_tags):
        tag_name = _normalized_tag_name(entry)
        if tag_name is None:
            continue
        tag_key = tag_name.casefold()
        if tag_key in seen:
            continue
        seen.add(tag_key)
        normalized_tags.append(tag_name)
    return normalized_tags


def _normalized_tag_name(entry: object) -> str | None:
    if not isinstance(entry, str):
        return None
    tag_name = entry.strip()
    return tag_name or None


def _update_validation_comment(
    *,
    validated_meta: _ValidatedSensitiveMeta,
    payload: ReportMetaJsonObject,
) -> None:
    if "validation_comment" not in payload:
        return
    validation_comment = payload.get("validation_comment")
    if not isinstance(validation_comment, str):
        validation_comment = ""
    if validated_meta.validation_comment != validation_comment:
        validated_meta.validation_comment = validation_comment
        validated_meta.save(update_fields=["validation_comment"])


def _apply_validation_tags(
    *,
    sensitive_meta: SensitiveMeta,
    payload: ReportMetaJsonObject,
) -> None:
    validated_meta = cast(_ValidatedSensitiveMeta, sensitive_meta)
    _update_validation_comment(validated_meta=validated_meta, payload=payload)
    if "tags" not in payload:
        return
    normalized_tags = _normalize_tag_names(payload.get("tags"))
    tag_objects = [Tag.objects.get_or_create(name=name)[0] for name in normalized_tags]
    validated_meta.tags.set(tag_objects)


def _validated_pdf_document_type(
    payload: ReportMetaJsonObject,
) -> tuple[str, DocumentTypeContract] | Response:
    document_type_name = payload.get("document_type")
    if not isinstance(document_type_name, str) or not document_type_name:
        return Response(
            {
                "error": "document_type is required for pdf validation.",
                "allowed_document_types": DOCUMENT_TYPE_VALUES,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if document_type_name not in DOCUMENT_TYPE_VALUES:
        return Response(
            {
                "error": f"Unsupported document_type '{document_type_name}'.",
                "allowed_document_types": DOCUMENT_TYPE_VALUES,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    return document_type_name, DocumentTypeContract(document_type_name)


def _case_resolution_payload(
    resolution: AutoCaseResolutionResult,
) -> dict[str, object]:
    patient_examination = resolution.patient_examination
    return {
        "status": resolution.status,
        "patient_examination_id": (
            patient_examination.pk if patient_examination is not None else None
        ),
        "created": resolution.created,
        "reason": resolution.reason,
    }


def _status_after_validation(
    *,
    state_obj: _ValidationState | None,
    status_before: str | None,
    media_type: str,
) -> str | None:
    if state_obj is None:
        return status_before
    try:
        state_obj.refresh_from_db()
        return _state_status_value(state_obj) or status_before
    except Exception:
        logger.exception(
            "Failed to read %s anonymization_status after validation", media_type
        )
        return status_before


def _record_validation_operation(
    *,
    request: Request,
    file_id: int,
    media_type: str,
    status_before: str | None,
    status_after: str | None,
    operation_meta: ValidationOperationMetaPayload,
) -> None:
    record_operation(
        request,
        action=ACTION_ANONYMIZATION_VALIDATED,
        resource_type=media_type,
        resource_id=file_id,
        status_before=status_before or STATUS_PROCESSING,
        status_after=status_after or STATUS_ANONYMIZED,
        meta={
            "timestamp": operation_meta["timestamp"],
            "timestamp_source": operation_meta["timestamp_source"],
            "examination_date": operation_meta["examination_date"],
        },
    )


def _video_integrity_error(
    *,
    video_obj: _VideoValidationLike,
    video_state: object,
) -> Response | None:
    raw_video_meta = video_obj.meta
    video_meta = (
        cast(dict[str, Any], raw_video_meta) if isinstance(raw_video_meta, dict) else {}
    )
    if not getattr(video_state, "processing_error", False) and (
        video_meta.get("integrity_status") != "lost"
    ):
        return None
    return Response(
        {"error": "Video is marked failed/lost by media integrity."},
        status=status.HTTP_409_CONFLICT,
    )


def _run_video_metadata_validation(
    *,
    video_obj: _VideoValidationLike,
    payload: ReportMetaJsonObject,
    file_id: int,
) -> VideoValidationPayload | Response:
    prepared_payload = _prepare_video_validation_payload(payload, video_obj)
    try:
        ok = video_obj.validate_metadata_annotation(
            cast(VideoTextMetaPayload, prepared_payload)
        )
    except Exception:  # pragma: no cover - defensive safety net
        transaction.set_rollback(True)
        logger.exception("Video validation crashed for id=%s", file_id)
        return Response(
            {"error": "Video validation encountered an unexpected error."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if ok:
        return prepared_payload
    transaction.set_rollback(True)
    return Response(
        {"error": "Video validation failed."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _ensure_video_sensitive_meta(
    *,
    video_obj: _VideoValidationLike,
    initial_sensitive_meta: SensitiveMeta | None,
) -> SensitiveMeta | Response:
    sensitive_meta = initial_sensitive_meta
    if video_obj.sensitive_meta is None:
        sensitive_meta = SensitiveMeta.objects.create(center=video_obj.center)
        video_obj.sensitive_meta = sensitive_meta
    if sensitive_meta is not None:
        return sensitive_meta
    transaction.set_rollback(True)
    return Response(
        {"message": "Video not validated, failed to create SensitiveMeta."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _finalize_video_validation(
    *,
    request: Request,
    video: VideoFile,
    video_obj: _VideoValidationLike,
    sensitive_meta: SensitiveMeta,
    payload: ReportMetaJsonObject,
) -> AutoCaseResolutionResult | Response:
    video_obj.save(update_fields=["sensitive_meta"])
    sensitive_meta.get_or_create_state()
    _apply_validation_tags(sensitive_meta=sensitive_meta, payload=payload)
    state_obj = sensitive_meta.state
    if state_obj is None:
        transaction.set_rollback(True)
        return Response(
            {"message": "Video not validated, failed to create State."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    state_obj.refresh_from_db()
    state_obj.mark_dob_verified()
    state_obj.mark_names_verified()
    resolution = commit_validated_media_identity(
        media_type="video",
        media_obj=video,
        user=_request_actor(request),
        source="anonymization_validate",
    )
    sensitive_meta.create_anonymized_record()
    if video_obj.state is not None:
        video_obj.state.save(update_fields=["anonymized"])
        state_obj.save()
    return resolution


def _record_video_validation_metrics(
    *,
    request: Request,
    video: VideoFile,
    prepared_payload: VideoValidationPayload,
    payload: ReportMetaJsonObject,
    before_values: dict[str, Any],
    status_before: str | None,
    status_after: str | None,
) -> None:
    metric_payload = prepared_payload.model_dump(mode="json", exclude_none=True)
    metric_payload["no_more_names_confirmed"] = payload.get("no_more_names_confirmed")
    record_validation_metrics(
        request=request,
        media_obj=video,
        media_type="video",
        payload=metric_payload,
        before_values=before_values,
        status_before=status_before or STATUS_PROCESSING,
        status_after=status_after or STATUS_ANONYMIZED,
    )


def _validate_video(
    *,
    request: Request,
    video: VideoFile,
    file_id: int,
    payload: ReportMetaJsonObject,
    operation_meta: ValidationOperationMetaPayload,
) -> Response:
    video_obj = cast(_VideoValidationLike, video)
    integrity_error = _video_integrity_error(
        video_obj=video_obj,
        video_state=get_or_create_video_state(video),
    )
    if integrity_error is not None:
        return integrity_error

    status_before = _state_status_value(video_obj.state)
    initial_sensitive_meta = video_obj.sensitive_meta
    before_values = capture_sensitive_meta_metric_values(
        sensitive_meta=initial_sensitive_meta,
        media_obj=video,
        media_type="video",
    )
    prepared_payload = _run_video_metadata_validation(
        video_obj=video_obj,
        payload=payload,
        file_id=file_id,
    )
    if isinstance(prepared_payload, Response):
        return prepared_payload

    sensitive_meta = _ensure_video_sensitive_meta(
        video_obj=video_obj,
        initial_sensitive_meta=initial_sensitive_meta,
    )
    if isinstance(sensitive_meta, Response):
        return sensitive_meta
    resolution = _finalize_video_validation(
        request=request,
        video=video,
        video_obj=video_obj,
        sensitive_meta=sensitive_meta,
        payload=payload,
    )
    if isinstance(resolution, Response):
        return resolution

    status_after = _status_after_validation(
        state_obj=video_obj.state,
        status_before=status_before,
        media_type="video",
    )
    _record_video_validation_metrics(
        request=request,
        video=video,
        prepared_payload=prepared_payload,
        payload=payload,
        before_values=before_values,
        status_before=status_before,
        status_after=status_after,
    )
    _record_validation_operation(
        request=request,
        file_id=file_id,
        media_type="video",
        status_before=status_before,
        status_after=status_after,
        operation_meta=operation_meta,
    )
    return Response(
        {
            "message": "Video validated.",
            "timestamp": operation_meta["timestamp"],
            "case_resolution": _case_resolution_payload(resolution),
        },
        status=status.HTTP_200_OK,
    )


def _run_pdf_metadata_validation(
    *,
    pdf: RawPdfFile,
    pdf_obj: _PdfValidationLike,
    payload: ReportMetaJsonObject,
    file_id: int,
) -> ReportMetaJsonObject | Response:
    prepared_payload = _prepare_validation_payload(payload, pdf_obj)
    try:
        ok = validate_report_metadata_annotation(
            pdf,
            prepared_payload,
            enforce_processed_artifact=False,
        )
    except Exception:  # pragma: no cover - defensive safety net
        transaction.set_rollback(True)
        logger.exception("report validation crashed for id=%s", file_id)
        return Response(
            {"error": "report validation encountered an unexpected error."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    _log_missing_pdf_validation_state(pdf)
    if ok:
        return prepared_payload
    transaction.set_rollback(True)
    return Response(
        {"error": "report validation failed."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _log_missing_pdf_validation_state(pdf: RawPdfFile) -> None:
    try:
        assert pdf.sensitive_meta is not None
        assert pdf.sensitive_meta.state is not None
    except AssertionError as error:
        logger.error("%s", error)


def _ensure_pdf_sensitive_meta(
    pdf_obj: _PdfValidationLike,
) -> SensitiveMeta:
    sensitive_meta = pdf_obj.sensitive_meta
    if sensitive_meta is None:
        sensitive_meta = SensitiveMeta.objects.create(center=pdf_obj.center)
        pdf_obj.sensitive_meta = sensitive_meta
    pdf_obj.save(update_fields=["sensitive_meta"])
    return sensitive_meta


def _finalize_pdf_validation(
    *,
    request: Request,
    pdf: RawPdfFile,
    pdf_obj: _PdfValidationLike,
    sensitive_meta: SensitiveMeta,
    payload: ReportMetaJsonObject,
) -> AutoCaseResolutionResult | Response:
    sensitive_meta.get_or_create_state()
    _apply_validation_tags(sensitive_meta=sensitive_meta, payload=payload)
    state_obj = sensitive_meta.state
    if state_obj is None:
        transaction.set_rollback(True)
        return Response(
            {"message": "report not validated, failed to create State."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    state_obj.refresh_from_db()
    state_obj.mark_dob_verified()
    state_obj.mark_names_verified()
    resolution = commit_validated_media_identity(
        media_type="pdf",
        media_obj=pdf,
        user=_request_actor(request),
        source="anonymization_validate",
    )
    sensitive_meta.create_anonymized_record()
    if pdf_obj.state is not None:
        pdf_obj.state.mark_anonymized()
        pdf_obj.state.save(update_fields=["anonymized"])
    state_obj.save()
    return resolution


def _materialize_linked_pdf_report(
    *,
    pdf: RawPdfFile,
    pdf_obj: _PdfValidationLike,
    resolution: AutoCaseResolutionResult,
) -> None:
    if resolution.status != "linked" or pdf_obj.examination_id is None:
        return
    upsert_anonym_examination_report_from_pdf(
        pdf=pdf,
        validated_at_iso=None,
        source="anonymization_validate_auto_case_resolution",
    )


def _record_pdf_validation_metrics(
    *,
    request: Request,
    pdf: RawPdfFile,
    prepared_payload: ReportMetaJsonObject,
    payload: ReportMetaJsonObject,
    before_values: dict[str, Any],
    status_before: str | None,
    status_after: str | None,
) -> None:
    metric_payload = dict(prepared_payload)
    metric_payload["no_more_names_confirmed"] = payload.get("no_more_names_confirmed")
    record_validation_metrics(
        request=request,
        media_obj=pdf,
        media_type="pdf",
        payload=metric_payload,
        before_values=before_values,
        status_before=status_before or STATUS_PROCESSING,
        status_after=status_after or STATUS_ANONYMIZED,
    )


def _pdf_success_response(
    *,
    pdf: RawPdfFile,
    pdf_obj: _PdfValidationLike,
    document_type_name: str,
    resolved_text: str,
    resolution: AutoCaseResolutionResult,
    timestamp: str,
) -> Response:
    report_file = None
    if pdf_obj.anonym_examination_report_id is not None:
        report_file = {
            "id": pdf_obj.anonym_examination_report_id,
            "document_type": document_type_name,
            "created": False,
        }
    return Response(
        {
            "message": "report validated.",
            "timestamp": timestamp,
            "report_file": report_file,
            "anonymized_text_saved": bool(resolved_text),
            "validation_context": _build_pdf_validation_context(pdf),
            "case_resolution": _case_resolution_payload(resolution),
        },
        status=status.HTTP_200_OK,
    )


def _validate_pdf(
    *,
    request: Request,
    pdf: RawPdfFile,
    file_id: int,
    payload: ReportMetaJsonObject,
    operation_meta: ValidationOperationMetaPayload,
) -> Response:
    pdf_obj = cast(_PdfValidationLike, pdf)
    document_type_result = _validated_pdf_document_type(payload)
    if isinstance(document_type_result, Response):
        return document_type_result
    document_type_name, document_type = document_type_result

    status_before = _state_status_value(pdf_obj.state)
    before_values = capture_sensitive_meta_metric_values(
        sensitive_meta=pdf_obj.sensitive_meta,
        media_obj=pdf,
        media_type="pdf",
    )
    prepared_payload = _run_pdf_metadata_validation(
        pdf=pdf,
        pdf_obj=pdf_obj,
        payload=payload,
        file_id=file_id,
    )
    if isinstance(prepared_payload, Response):
        return prepared_payload
    resolution = _finalize_pdf_validation(
        request=request,
        pdf=pdf,
        pdf_obj=pdf_obj,
        sensitive_meta=_ensure_pdf_sensitive_meta(pdf_obj),
        payload=payload,
    )
    if isinstance(resolution, Response):
        return resolution

    resolved_text = _persist_pdf_validation_state(
        pdf=pdf,
        payload=prepared_payload,
        validated_at_iso=operation_meta["timestamp"],
        document_type=document_type,
    )
    _materialize_linked_pdf_report(
        pdf=pdf,
        pdf_obj=pdf_obj,
        resolution=resolution,
    )
    status_after = _status_after_validation(
        state_obj=pdf_obj.state,
        status_before=status_before,
        media_type="pdf",
    )
    _record_pdf_validation_metrics(
        request=request,
        pdf=pdf,
        prepared_payload=prepared_payload,
        payload=payload,
        before_values=before_values,
        status_before=status_before,
        status_after=status_after,
    )
    _record_validation_operation(
        request=request,
        file_id=file_id,
        media_type="pdf",
        status_before=status_before,
        status_after=status_after,
        operation_meta=operation_meta,
    )
    return _pdf_success_response(
        pdf=pdf,
        pdf_obj=pdf_obj,
        document_type_name=document_type_name,
        resolved_text=resolved_text,
        resolution=resolution,
        timestamp=operation_meta["timestamp"],
    )


def _video_by_id(file_id: int) -> VideoFile | None:
    return (
        VideoFile.objects.select_related("center", "sensitive_meta", "state")
        .filter(pk=file_id)
        .first()
    )


def _pdf_by_id(file_id: int) -> RawPdfFile | None:
    return (
        RawPdfFile.objects.select_related("center", "sensitive_meta", "state")
        .filter(pk=file_id)
        .first()
    )


def _validate_video_or_not_found(
    *,
    request: Request,
    file_id: int,
    payload: ReportMetaJsonObject,
    operation_meta: ValidationOperationMetaPayload,
) -> Response:
    video = _video_by_id(file_id)
    if video is not None:
        return _validate_video(
            request=request,
            video=video,
            file_id=file_id,
            payload=payload,
            operation_meta=operation_meta,
        )
    return Response(
        {"error": f"Video {file_id} not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _validate_pdf_or_not_found(
    *,
    request: Request,
    file_id: int,
    payload: ReportMetaJsonObject,
    operation_meta: ValidationOperationMetaPayload,
) -> Response:
    pdf = _pdf_by_id(file_id)
    if pdf is not None:
        return _validate_pdf(
            request=request,
            pdf=pdf,
            file_id=file_id,
            payload=payload,
            operation_meta=operation_meta,
        )
    return Response(
        {"error": f"report {file_id} not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _dispatch_validation(
    *,
    request: Request,
    file_id: int,
    payload: ReportMetaJsonObject,
    operation_meta: ValidationOperationMetaPayload,
) -> Response:
    file_type = payload.get("file_type")
    if file_type == "video":
        return _validate_video_or_not_found(
            request=request,
            file_id=file_id,
            payload=payload,
            operation_meta=operation_meta,
        )
    if file_type == "pdf":
        return _validate_pdf_or_not_found(
            request=request,
            file_id=file_id,
            payload=payload,
            operation_meta=operation_meta,
        )

    video = _video_by_id(file_id)
    if video is not None:
        return _validate_video(
            request=request,
            video=video,
            file_id=file_id,
            payload=payload,
            operation_meta=operation_meta,
        )
    pdf = _pdf_by_id(file_id)
    if pdf is not None:
        return _validate_pdf(
            request=request,
            pdf=pdf,
            file_id=file_id,
            payload=payload,
            operation_meta=operation_meta,
        )
    return Response(
        {"error": f"Item {file_id} not found as video or pdf."},
        status=status.HTTP_404_NOT_FOUND,
    )


class AnonymizationValidateView(APIView):
    """
    POST /api/anonymization/<int:file_id>/validate/

    Validiert und aktualisiert SensitiveMeta-Felder für Videos oder reports.

    DATA HERE IS COMING FROM THE ANONYIZATION VALIDATION COMPONENT

    Body (Datumsfelder bevorzugt in deutschem Format DD.MM.YYYY; ISO YYYY-MM-DD ebenfalls akzeptiert):
    {
      "patient_first_name": "Max",
      "patient_last_name":  "Mustermann",
      "patient_dob":        "21.03.1994",      // DD.MM.YYYY bevorzugt
      "patient_gender":     "male"
      "examination_date":   "15.02.2024",      // DD.MM.YYYY bevorzugt

      "casenumber":         "12345",
      "anonymized_text":    "...",             // nur für reports; Videos ignorieren
      "is_verified":        true               // optional; default true
      "file_type":        "video"            // optional; "video" oder "pdf"; wenn nicht angegeben, wird zuerst Video, dann report versucht
      "center_name":       edited_patient.value.center_name || '',
      "external_id":       editedPatient.value.externalId || '',
      "external_id_origin":editedPatient.value.externalIdOrigin || '',
    }

    Rückwärtskompatibilität: ISO-Format (YYYY-MM-DD) wird ebenfalls akzeptiert.
    """

    @transaction.atomic
    def post(self, request: Request, file_id: int) -> Response:
        serializer = SensitiveMetaValidateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        payload: ReportMetaJsonObject = dict(serializer.validated_data)
        payload.setdefault("is_verified", True)
        operation_meta = _validation_operation_meta(payload)

        with transaction.atomic():
            return _dispatch_validation(
                request=request,
                file_id=file_id,
                payload=payload,
                operation_meta=operation_meta,
            )
