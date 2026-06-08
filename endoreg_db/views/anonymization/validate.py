import logging
from datetime import date as dt_date, datetime, time as dt_time
from typing import Any, Dict, cast

from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts import DocumentType as DocumentTypeContract
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
from endoreg_db.utils.web.permissions import EnvironmentAwarePermission
from endoreg_db.utils.observability.operation_log import (
    record_operation,
    ACTION_ANONYMIZATION_VALIDATED,
    STATUS_PROCESSING,
    STATUS_ANONYMIZED,
)


logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([EnvironmentAwarePermission])
def anonymization_document_types_dropdown(_request):
    ensure_document_types()
    return Response(
        [{"value": value, "label": value} for value in DOCUMENT_TYPE_VALUES],
        status=status.HTTP_200_OK,
    )


def _state_status_value(state_obj: Any) -> str | None:
    """Return anonymization status as string if present, else None."""
    if state_obj is None:
        return None
    st = getattr(state_obj, "anonymization_status", None)
    if st is None:
        return None
    return str(getattr(st, "value", st))


def _preferred_validation_timestamp(payload: Dict[str, Any]) -> tuple[str, str]:
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


def _validation_operation_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    timestamp, source = _preferred_validation_timestamp(payload)
    meta: Dict[str, Any] = {
        "timestamp": timestamp,
        "timestamp_source": source,
    }
    exam_date = payload.get("examination_date")
    if isinstance(exam_date, (dt_date, datetime)):
        if isinstance(exam_date, datetime):
            meta["examination_date"] = exam_date.date().isoformat()
        else:
            meta["examination_date"] = exam_date.isoformat()
    return meta


def _persist_pdf_validation_state(
    *,
    pdf: RawPdfFile,
    payload: Dict[str, Any],
    validated_at_iso: str,
    document_type: DocumentTypeContract,
) -> str:
    report_context = build_report_context_from_validation(
        pdf=pdf,
        payload=payload,
        document_type_name=document_type,
    )
    resolved_text = report_context.anonymized_text
    raw_meta: Dict[str, Any]
    if isinstance(pdf.raw_meta, dict):
        raw_meta = dict(pdf.raw_meta)
    else:
        raw_meta = {}

    sensitive_meta = pdf.sensitive_meta
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
    if getattr(pdf, "anonymized_text", None) != resolved_text:
        pdf.anonymized_text = resolved_text
        update_fields.append("anonymized_text")
    if (
        pdf.center_id is None
        and sensitive_meta is not None
        and sensitive_meta.center_id
    ):
        pdf.center = sensitive_meta.center
        update_fields.append("center")
    if pdf.raw_meta != raw_meta:
        pdf.raw_meta = raw_meta
        update_fields.append("raw_meta")
    if update_fields:
        pdf.save(update_fields=update_fields)

    return resolved_text


def _build_pdf_validation_context(pdf: RawPdfFile) -> Dict[str, Any] | None:
    sensitive_meta = pdf.sensitive_meta
    if sensitive_meta is None:
        return None

    return {
        "sensitive_meta_id": sensitive_meta.pk,
        "patient_hash_display": (
            f"...{sensitive_meta.patient_hash[-8:]}"
            if sensitive_meta.patient_hash
            else None
        ),
        "examination_hash_display": (
            f"...{sensitive_meta.examination_hash[-8:]}"
            if sensitive_meta.examination_hash
            else None
        ),
        "pseudo_patient_id": sensitive_meta.pseudo_patient_id,
        "pseudo_examination_id": sensitive_meta.pseudo_examination_id,
    }


def _normalize_tag_names(raw_tags: Any) -> list[str]:
    if not isinstance(raw_tags, list):
        return []

    normalized_tags: list[str] = []
    seen: set[str] = set()
    for entry in raw_tags:
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
    payload: Dict[str, Any],
) -> None:
    update_fields: list[str] = []

    if "validation_comment" in payload:
        validation_comment = payload.get("validation_comment")
        if not isinstance(validation_comment, str):
            validation_comment = ""
        if sensitive_meta.validation_comment != validation_comment:
            sensitive_meta.validation_comment = validation_comment
            update_fields.append("validation_comment")

    if update_fields:
        sensitive_meta.save(update_fields=update_fields)

    if "tags" not in payload:
        return

    normalized_tags = _normalize_tag_names(payload.get("tags"))
    tag_objects = [Tag.objects.get_or_create(name=name)[0] for name in normalized_tags]
    sensitive_meta.tags.set(tag_objects)


def _validated_pdf_document_type(
    payload: Dict[str, Any],
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
    def post(self, request: Request, file_id: int):
        # Serializer-Validierung mit deutscher Datums-Priorität
        serializer = SensitiveMetaValidateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        validated_data = cast(Dict[str, Any], serializer.validated_data)
        payload: Dict[str, Any] = dict(validated_data)

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
                    video_state = get_or_create_video_state(video)
                    video_meta = video.meta if isinstance(video.meta, dict) else {}
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
                    status_before = _state_status_value(video.state)
                    before_values = capture_sensitive_meta_metric_values(
                        sensitive_meta=video.sensitive_meta,
                        media_obj=video,
                        media_type="video",
                    )
                    prepared_payload = self._prepare_payload(payload, video)
                    try:
                        ok = video.validate_metadata_annotation(prepared_payload)
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
                    if video.sensitive_meta is None:
                        sm = SensitiveMeta.objects.create(center=video.center)
                        video.sensitive_meta = sm

                    video.save(update_fields=["sensitive_meta"])
                    video.sensitive_meta.get_or_create_state()
                    _apply_validation_tags(
                        sensitive_meta=video.sensitive_meta,
                        payload=payload,
                    )
                    if video.sensitive_meta.state is not None:
                        video.sensitive_meta.state.refresh_from_db()
                        video.sensitive_meta.state.mark_dob_verified()
                        video.sensitive_meta.state.mark_names_verified()
                        auto_case_resolution = commit_validated_media_identity(
                            media_type="video",
                            media_obj=video,
                            user=request.user,
                            source="anonymization_validate",
                        )
                        video.sensitive_meta.create_anonymized_record()
                    else:
                        transaction.set_rollback(True)
                        return Response(
                            {"message": "Video not validated, failed to create State."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        )

                    if video.state is not None:
                        video.state.anonymized = True
                        video.state.save(update_fields=["anonymized"])
                        video.sensitive_meta.state.save()

                        # --- NEW: status AFTER validation ---
                    status_after = status_before
                    try:
                        if video.state is not None:
                            video.state.refresh_from_db()
                            status_after = (
                                _state_status_value(video.state) or status_after
                            )
                    except Exception:
                        logger.exception(
                            "Failed to read video anonymization_status after validation"
                        )

                    metric_payload = dict(prepared_payload)
                    metric_payload["no_more_names_confirmed"] = payload.get(
                        "no_more_names_confirmed"
                    )
                    record_validation_metrics(
                        request=request,
                        media_obj=video,
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
                        meta=operation_meta,
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
                    document_type_result = _validated_pdf_document_type(payload)
                    if isinstance(document_type_result, Response):
                        return document_type_result
                    document_type_name, document_type = document_type_result

                    status_before = _state_status_value(pdf.state)
                    before_values = capture_sensitive_meta_metric_values(
                        sensitive_meta=pdf.sensitive_meta,
                        media_obj=pdf,
                        media_type="pdf",
                    )
                    prepared_payload = self._prepare_payload(payload, pdf)
                    try:
                        ok = validate_report_metadata_annotation(
                            pdf,
                            prepared_payload,
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
                        if pdf.sensitive_meta is None:
                            sm = SensitiveMeta.objects.create(center=pdf.center)
                            pdf.sensitive_meta = sm

                        pdf.save(update_fields=["sensitive_meta"])
                        pdf.sensitive_meta.get_or_create_state()
                        _apply_validation_tags(
                            sensitive_meta=pdf.sensitive_meta,
                            payload=payload,
                        )
                        if pdf.sensitive_meta and pdf.sensitive_meta.state:
                            state_obj = cast(Any, pdf.sensitive_meta.state)
                            state_obj.refresh_from_db()
                            state_obj.mark_dob_verified()
                            state_obj.mark_names_verified()
                            auto_case_resolution = commit_validated_media_identity(
                                media_type="pdf",
                                media_obj=pdf,
                                user=request.user,
                                source="anonymization_validate",
                            )
                            pdf.sensitive_meta.create_anonymized_record()

                            if pdf.state:
                                pdf.state.mark_anonymized()
                                pdf.state.save(update_fields=["anonymized"])

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
                            pdf=pdf,
                            payload=prepared_payload,
                            validated_at_iso=response_timestamp,
                            document_type=document_type,
                        )

                    if (
                        auto_case_resolution.status == "linked"
                        and pdf.examination_id is not None
                    ):
                        upsert_anonym_examination_report_from_pdf(
                            pdf=pdf,
                            validated_at_iso=None,
                            source="anonymization_validate_auto_case_resolution",
                        )

                    status_after = status_before
                    try:
                        if pdf.state is not None:
                            pdf.state.refresh_from_db()
                            status_after = (
                                _state_status_value(pdf.state) or status_after
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
                        media_obj=pdf,
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
                        meta=operation_meta,
                    )

                    return Response(
                        {
                            "message": "report validated.",
                            "timestamp": response_timestamp,
                            "report_file": (
                                {
                                    "id": pdf.anonym_examination_report_id,
                                    "document_type": document_type_name,
                                    "created": False,
                                }
                                if pdf.anonym_examination_report_id is not None
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
    def _prepare_payload(base_payload: Dict[str, Any], file_obj: Any) -> Dict[str, Any]:
        """
        Return a fresh payload tailored for the given file object.

        - Strips `file_type` before forwarding to validators.
        - Injects `center_name` from the file's center if not already present.
        - Normalizes `patient_gender` if present, but does NOT require it.
        """
        prepared: Dict[str, Any] = dict(base_payload)

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
