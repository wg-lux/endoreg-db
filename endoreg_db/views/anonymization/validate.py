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


def _persist_pdf_validation_state(
    *,
    pdf: RawPdfFile,
    payload: ReportMetaJsonObject,
    validated_at_iso: str,
    document_type: DocumentTypeContract,
) -> str:
    pdf_obj = cast(_PdfValidationLike, pdf)
    original_anonymized_text = getattr(pdf_obj, "anonymized_text", None)
    if original_anonymized_text is None:
        payload_text = payload.get("anonymized_text")
        pdf_obj.anonymized_text = payload_text if isinstance(payload_text, str) else ""

    report_context = build_report_context_from_validation(
        pdf=pdf,
        payload=payload,
        document_type_name=document_type,
    )
    resolved_text = report_context.anonymized_text
    raw_meta: ReportMetaJsonObject
    if isinstance(pdf_obj.raw_meta, dict):
        raw_meta = cast(ReportMetaJsonObject, dict(pdf_obj.raw_meta))
    else:
        raw_meta = cast(ReportMetaJsonObject, {})

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

    update_fields: list[str] = []
    if original_anonymized_text != resolved_text:
        pdf.anonymized_text = resolved_text
        update_fields.append("anonymized_text")
    if (
        getattr(pdf_obj, "center_id", None) is None
        and sensitive_meta is not None
        and getattr(sensitive_meta, "center_id", None)
    ):
        pdf_obj.center = cast(_CenterLike, getattr(sensitive_meta, "center"))
        update_fields.append("center")
    if pdf_obj.raw_meta != raw_meta:
        pdf_obj.raw_meta = raw_meta
        update_fields.append("raw_meta")
    if update_fields:
        pdf_obj.save(update_fields=update_fields)

    return resolved_text


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
        if not isinstance(entry, str):
            continue
        tag_name = entry.strip()
        if not tag_name:
            continue
        tag_key = tag_name.casefold()
        if tag_key in seen:
            continue
        seen.add(tag_key)
        normalized_tags.append(tag_name)
    return normalized_tags


def _apply_validation_tags(
    *,
    sensitive_meta: SensitiveMeta,
    payload: ReportMetaJsonObject,
) -> None:
    validated_meta = cast(_ValidatedSensitiveMeta, sensitive_meta)
    update_fields: list[str] = []

    if "validation_comment" in payload:
        validation_comment = payload.get("validation_comment")
        if not isinstance(validation_comment, str):
            validation_comment = ""
        if validated_meta.validation_comment != validation_comment:
            validated_meta.validation_comment = validation_comment
            update_fields.append("validation_comment")

    if update_fields:
        validated_meta.save(update_fields=update_fields)

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
        # Serializer-Validierung mit deutscher Datums-Priorität
        serializer = SensitiveMetaValidateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        payload: ReportMetaJsonObject = dict(validated_data)

        # Default ist_verified = True
        if "is_verified" not in payload:
            payload["is_verified"] = True

        file_type = payload.get("file_type")
        status_before = None
        operation_meta = _validation_operation_meta(payload)
        response_timestamp = operation_meta["timestamp"]

        with transaction.atomic():
            # Try Video first (unless explicitly requesting report)
            if file_type in (None, "video"):
                video = (
                    VideoFile.objects.select_related(
                        "center", "sensitive_meta", "state"
                    )
                    .filter(pk=file_id)
                    .first()
                )
                # TODO: The state for video will be none when no state is set and the state for pdf will always be none. After status needs to be inferred after calling the sensitive meta state update functions
                if video is not None:
                    video_obj = cast(_VideoValidationLike, video)
                    video_state = get_or_create_video_state(video)
                    raw_video_meta = video_obj.meta
                    video_meta = (
                        cast(dict[str, Any], raw_video_meta)
                        if isinstance(raw_video_meta, dict)
                        else {}
                    )
                    if getattr(video_state, "processing_error", False) or (
                        video_meta.get("integrity_status") == "lost"
                    ):
                        return Response(
                            {
                                "error": (
                                    "Video is marked failed/lost by media integrity."
                                )
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                    status_before = _state_status_value(video_obj.state)
                    video_sensitive_meta: SensitiveMeta | None = (
                        video_obj.sensitive_meta
                    )
                    before_values = capture_sensitive_meta_metric_values(
                        sensitive_meta=video_sensitive_meta,
                        media_obj=video,
                        media_type="video",
                    )
                    prepared_payload = self._prepare_video_payload(payload, video_obj)
                    try:
                        ok = video_obj.validate_metadata_annotation(
                            cast(VideoTextMetaPayload, prepared_payload)
                        )
                    except Exception:  # pragma: no cover - defensive safety net
                        transaction.set_rollback(True)
                        logger.exception("Video validation crashed for id=%s", file_id)
                        return Response(
                            {
                                "error": "Video validation encountered an unexpected error."
                            },
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    if not ok:
                        transaction.set_rollback(True)
                        return Response(
                            {"error": "Video validation failed."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    # this is here for tests!
                    if video_obj.sensitive_meta is None:
                        sm = SensitiveMeta.objects.create(center=video_obj.center)
                        video_sensitive_meta = sm
                        video_obj.sensitive_meta = video_sensitive_meta

                    if video_sensitive_meta is None:
                        transaction.set_rollback(True)
                        return Response(
                            {
                                "message": "Video not validated, failed to create SensitiveMeta."
                            },
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    video_obj.save(update_fields=["sensitive_meta"])
                    video_sensitive_meta.get_or_create_state()
                    _apply_validation_tags(
                        sensitive_meta=video_sensitive_meta,
                        payload=payload,
                    )
                    if video_sensitive_meta.state is None:
                        transaction.set_rollback(True)
                        return Response(
                            {"message": "Video not validated, failed to create State."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    state_obj = video_sensitive_meta.state
                    state_obj.refresh_from_db()
                    state_obj.mark_dob_verified()
                    state_obj.mark_names_verified()
                    auto_case_resolution = commit_validated_media_identity(
                        media_type="video",
                        media_obj=video,
                        user=_request_actor(request),
                        source="anonymization_validate",
                    )
                    video_sensitive_meta.create_anonymized_record()

                    if video_obj.state is not None:
                        video_obj.state.save(update_fields=["anonymized"])
                        video_sensitive_meta.state.save()

                    # --- NEW: status AFTER validation ---
                    status_after = status_before
                    try:
                        if video_obj.state is not None:
                            video_obj.state.refresh_from_db()
                            status_after = (
                                _state_status_value(video_obj.state) or status_after
                            )
                    except Exception:
                        logger.exception(
                            "Failed to read video anonymization_status after validation"
                        )

                    metric_payload = prepared_payload.model_dump(
                        mode="json", exclude_none=True
                    )
                    metric_payload["no_more_names_confirmed"] = payload.get(
                        "no_more_names_confirmed"
                    )
                    record_validation_metrics(
                        request=request,
                        media_obj=cast(VideoFile, video_obj),
                        media_type="video",
                        payload=metric_payload,
                        before_values=before_values,
                        status_before=status_before or STATUS_PROCESSING,
                        status_after=status_after or STATUS_ANONYMIZED,
                    )

                    # --- write operation log ---
                    # TODO: update the function call bases on the status , once merged
                    record_operation(
                        request,
                        action=ACTION_ANONYMIZATION_VALIDATED,
                        resource_type="video",
                        resource_id=file_id,
                        status_before=status_before or STATUS_PROCESSING,
                        status_after=status_after or STATUS_ANONYMIZED,
                        meta={
                            "timestamp": operation_meta["timestamp"],
                            "timestamp_source": operation_meta["timestamp_source"],
                            "examination_date": operation_meta["examination_date"],
                        },
                    )

                    return Response(
                        {
                            "message": "Video validated.",
                            "timestamp": response_timestamp,
                            "case_resolution": {
                                "status": auto_case_resolution.status,
                                "patient_examination_id": (
                                    auto_case_resolution.patient_examination.pk
                                    if auto_case_resolution.patient_examination
                                    else None
                                ),
                                "created": auto_case_resolution.created,
                                "reason": auto_case_resolution.reason,
                            },
                        },
                        status=status.HTTP_200_OK,
                    )

                if file_type == "video":
                    return Response(
                        {"error": f"Video {file_id} not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

            # Then report (unless explicitly requesting Video)
            if file_type in (None, "pdf"):
                pdf = (
                    RawPdfFile.objects.select_related(
                        "center", "sensitive_meta", "state"
                    )
                    .filter(pk=file_id)
                    .first()
                )
                if pdf is not None:
                    pdf_obj = cast(_PdfValidationLike, pdf)
                    document_type_result = _validated_pdf_document_type(payload)
                    if isinstance(document_type_result, Response):
                        return document_type_result
                    document_type_name, document_type = document_type_result

                    status_before = _state_status_value(pdf_obj.state)
                    before_values = capture_sensitive_meta_metric_values(
                        sensitive_meta=pdf_obj.sensitive_meta,
                        media_obj=cast(RawPdfFile, pdf_obj),
                        media_type="pdf",
                    )
                    prepared_payload = self._prepare_payload(payload, pdf_obj)
                    try:
                        ok = validate_report_metadata_annotation(
                            cast(RawPdfFile, pdf_obj),
                            prepared_payload,
                            enforce_processed_artifact=False,
                        )
                    except Exception:  # pragma: no cover - defensive safety net
                        transaction.set_rollback(True)
                        logger.exception("report validation crashed for id=%s", file_id)
                        return Response(
                            {
                                "error": "report validation encountered an unexpected error."
                            },
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    # sanity logging – but don't break flow
                    try:
                        assert pdf.sensitive_meta is not None
                        assert pdf.sensitive_meta.state is not None
                    except AssertionError as e:
                        logger.error("%s", e)

                    if not ok:
                        transaction.set_rollback(True)
                        return Response(
                            {"error": "report validation failed."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    else:
                        # this is here for tests!
                        pdf_sensitive_meta = pdf_obj.sensitive_meta
                        if pdf_sensitive_meta is None:
                            sm = SensitiveMeta.objects.create(center=pdf_obj.center)
                            pdf_sensitive_meta = sm
                            pdf_obj.sensitive_meta = pdf_sensitive_meta

                        pdf_obj.save(update_fields=["sensitive_meta"])
                        pdf_sensitive_meta.get_or_create_state()
                        _apply_validation_tags(
                            sensitive_meta=pdf_sensitive_meta,
                            payload=payload,
                        )
                        if pdf_sensitive_meta.state is not None:
                            state_obj = pdf_sensitive_meta.state
                            state_obj.refresh_from_db()
                            state_obj.mark_dob_verified()
                            state_obj.mark_names_verified()
                            auto_case_resolution = commit_validated_media_identity(
                                media_type="pdf",
                                media_obj=cast(RawPdfFile, pdf_obj),
                                user=_request_actor(request),
                                source="anonymization_validate",
                            )
                            pdf_sensitive_meta.create_anonymized_record()

                            if pdf_obj.state is not None:
                                pdf_obj.state.mark_anonymized()
                                pdf_obj.state.save(update_fields=["anonymized"])

                            state_obj.save()
                        else:
                            transaction.set_rollback(True)
                            return Response(
                                {
                                    "message": "report not validated, failed to create State."
                                },
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            )

                        resolved_text = _persist_pdf_validation_state(
                            pdf=cast(RawPdfFile, pdf_obj),
                            payload=prepared_payload,
                            validated_at_iso=response_timestamp,
                            document_type=document_type,
                        )

                    if (
                        auto_case_resolution.status == "linked"
                        and pdf_obj.examination_id is not None
                    ):
                        upsert_anonym_examination_report_from_pdf(
                            pdf=cast(RawPdfFile, pdf_obj),
                            validated_at_iso=None,
                            source="anonymization_validate_auto_case_resolution",
                        )

                    status_after = status_before
                    try:
                        if pdf_obj.state is not None:
                            pdf_obj.state.refresh_from_db()
                            status_after = (
                                _state_status_value(pdf_obj.state) or status_after
                            )
                    except Exception:
                        logger.exception(
                            "Failed to read pdf anonymization_status after validation"
                        )

                    metric_payload = dict(prepared_payload)
                    metric_payload["no_more_names_confirmed"] = payload.get(
                        "no_more_names_confirmed"
                    )
                    record_validation_metrics(
                        request=request,
                        media_obj=cast(RawPdfFile, pdf_obj),
                        media_type="pdf",
                        payload=metric_payload,
                        before_values=before_values,
                        status_before=status_before or STATUS_PROCESSING,
                        status_after=status_after or STATUS_ANONYMIZED,
                    )

                    # --- NEW: write operation log ---
                    record_operation(
                        request,
                        action=ACTION_ANONYMIZATION_VALIDATED,
                        resource_type="pdf",
                        resource_id=file_id,
                        status_before=status_before or STATUS_PROCESSING,
                        status_after=status_after or STATUS_ANONYMIZED,
                        meta={
                            "timestamp": operation_meta["timestamp"],
                            "timestamp_source": operation_meta["timestamp_source"],
                            "examination_date": operation_meta["examination_date"],
                        },
                    )

                    return Response(
                        {
                            "message": "report validated.",
                            "timestamp": response_timestamp,
                            "report_file": (
                                {
                                    "id": pdf_obj.anonym_examination_report_id,
                                    "document_type": document_type_name,
                                    "created": False,
                                }
                                if pdf_obj.anonym_examination_report_id is not None
                                else None
                            ),
                            "anonymized_text_saved": bool(resolved_text),
                            "validation_context": _build_pdf_validation_context(pdf),
                            "case_resolution": {
                                "status": auto_case_resolution.status,
                                "patient_examination_id": (
                                    auto_case_resolution.patient_examination.pk
                                    if auto_case_resolution.patient_examination
                                    else None
                                ),
                                "created": auto_case_resolution.created,
                                "reason": auto_case_resolution.reason,
                            },
                        },
                        status=status.HTTP_200_OK,
                    )

                if file_type == "pdf":
                    return Response(
                        {"error": f"report {file_id} not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )

        return Response(
            {"error": f"Item {file_id} not found as video or pdf."},
            status=status.HTTP_404_NOT_FOUND,
        )

    @staticmethod
    def _prepare_video_payload(
        base_payload: ReportMetaJsonObject,
        file_obj: object,
    ) -> VideoValidationPayload:
        prepared = AnonymizationValidateView._prepare_payload(base_payload, file_obj)
        validated_payload = VideoTextMetaPayload.model_validate(
            _video_text_meta_payload_data(prepared)
        )
        return VideoValidationPayload(validated_payload.model_dump(mode="python"))

    @staticmethod
    def _prepare_payload(
        base_payload: ReportMetaJsonObject,
        file_obj: object,
    ) -> ReportMetaJsonObject:
        """
        Return a fresh payload tailored for the given file object.

        - Strips `file_type` before forwarding to validators.
        - Injects `center_name` from the file's center if not already present.
        - Normalizes `patient_gender` if present, but does NOT require it.
        """
        prepared: ReportMetaJsonObject = dict(base_payload)

        # never send file_type to validators
        prepared.pop("file_type", None)
        prepared.pop("no_more_names_confirmed", None)

        # center_name from file.center if not already set
        center = getattr(file_obj, "center", None)
        center_name = getattr(center, "name", None)
        if center_name and not prepared.get("center_name"):
            prepared["center_name"] = center_name

        # Gender normalization: optional, robust against missing or unknown values
        raw_gender = base_payload.get("patient_gender", None)
        if raw_gender is None:
            # nothing provided → don't touch gender
            return prepared

        gender = str(raw_gender).strip().lower()

        # empty string behaves as "not set" – don't override anything
        if gender == "":
            return prepared

        male_values = {"m", "male", "männlich"}
        female_values = {"w", "f", "female", "weiblich"}

        if gender in male_values:
            prepared["patient_gender"] = "male"
        elif gender in female_values:
            prepared["patient_gender"] = "female"
        else:
            # Unsupported values are ignored to prevent incorrect metadata coercion.
            logger.warning(
                "Unsupported patient_gender value %r; leaving value unchanged",
                raw_gender,
            )
            prepared.pop("patient_gender", None)

        return prepared
