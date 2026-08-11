from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models.fields.files import FieldFile
from django.http import HttpRequest
from lx_dtypes.models.contracts import (
    PdfRedactionRequest,
    PdfRedactionResponse,
    ValidationError,
)
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.pdf_processing_history import PdfProcessingHistory
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.serializers.pdf.pdf_processing_history import (
    PdfProcessingHistorySerializer,
)
from endoreg_db.services.polling_coordinator import ProcessingLockContext
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.raw_pdf_files.integrity import (
    verify_and_persist_processed_report_sha256,
)
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.media_urls import build_pdf_stream_path
from endoreg_db.utils.operation_log import record_operation
from endoreg_db.utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)

MAX_REDACTION_UPLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
PDF_MAGIC_HEADER = b"%PDF-"


def _state_status_value(state_obj: Any) -> str | None:
    if state_obj is None:
        return None
    status_value = getattr(state_obj, "anonymization_status", None)
    if status_value is None:
        return None
    return str(getattr(status_value, "value", status_value))


def _request_data(request: Request) -> Mapping[str, Any]:
    data = cast(object, request.data)
    if isinstance(data, Mapping):
        return cast(Mapping[str, Any], data)
    return {}


def _uploaded_file(request: Request, field_name: str) -> UploadedFile | None:
    files = cast(Mapping[str, UploadedFile], request.FILES)
    return files.get(field_name)


def _django_request(request: Request) -> HttpRequest:
    return request._request


def _pdf_pk(pdf: RawPdfFile) -> int:
    value: object = pdf.pk
    return int(value)


def _pdf_hash(pdf: RawPdfFile) -> str:
    pdf_obj = cast(Any, pdf)
    return str(pdf_obj.pdf_hash)


def _pdf_file(pdf: RawPdfFile) -> FieldFile:
    return pdf.file


def _pdf_processed_file(pdf: RawPdfFile) -> FieldFile:
    return pdf.processed_file


def _pdf_state(pdf: RawPdfFile) -> RawPdfState | None:
    return pdf.state


def _pdf_updated_at(pdf: RawPdfFile) -> str:
    pdf_obj = cast(Any, pdf)
    date_modified: object = pdf_obj.date_modified
    if isinstance(date_modified, datetime):
        return date_modified.isoformat()
    return str(date_modified)


def _history_pk(history_entry: PdfProcessingHistory) -> int:
    value: object = history_entry.pk
    if isinstance(value, int):
        return value
    return int(str(value))


def _state_sensitive_meta_processed(state: RawPdfState) -> bool:
    state_obj = cast(Any, state)
    return bool(state_obj.sensitive_meta_processed)


def _save_pdf_after_processed_file(pdf: RawPdfFile) -> None:
    pdf_obj = cast(Any, pdf)
    pdf_obj.save(update_fields=["processed_file", "date_modified"])


def _save_pdf_state(state: RawPdfState, *, update_fields: list[str]) -> None:
    state_obj = cast(Any, state)
    state_obj.save(update_fields=update_fields)


def _save_processed_file(
    pdf: RawPdfFile,
    *,
    filename: str,
    uploaded_pdf: UploadedFile,
) -> None:
    save_file = cast(
        Callable[[str, UploadedFile, bool], None],
        cast(Any, _pdf_processed_file(pdf)).save,
    )
    save_file(filename, uploaded_pdf, False)


def _serializer_data(serializer: PdfProcessingHistorySerializer) -> object:
    return cast(object, cast(Any, serializer).data)


def _is_pdf_file(uploaded_file: UploadedFile) -> bool:
    try:
        uploaded_file.seek(0)
        header = uploaded_file.read(len(PDF_MAGIC_HEADER))
    finally:
        uploaded_file.seek(0)
    return header == PDF_MAGIC_HEADER


def _sha256_uploaded_file(uploaded_file: UploadedFile) -> str:
    digest = hashlib.sha256()
    try:
        uploaded_file.seek(0)
        chunks = cast(Callable[[], Iterable[bytes]], cast(Any, uploaded_file).chunks)
        for chunk in chunks():
            digest.update(chunk)
    finally:
        uploaded_file.seek(0)
    return digest.hexdigest()


@dataclass(frozen=True)
class _PreparedRedaction:
    uploaded_pdf: UploadedFile
    payload: PdfRedactionRequest
    manifest: dict[str, object]
    source_sha256: str
    processed_filename: str


@dataclass(frozen=True)
class _PersistedRedaction:
    pdf: RawPdfFile
    history_entry: PdfProcessingHistory


def _validate_uploaded_pdf(request: Request) -> UploadedFile | Response:
    uploaded_pdf = _uploaded_file(request, "file")
    if uploaded_pdf is None:
        return Response(
            {"error": "file is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    file_size = getattr(uploaded_pdf, "size", 0)
    if isinstance(file_size, int) and file_size > MAX_REDACTION_UPLOAD_BYTES:
        return Response(
            {
                "error": (
                    "uploaded file exceeds maximum size of "
                    f"{MAX_REDACTION_UPLOAD_BYTES} bytes."
                )
            },
            status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    content_type = str(getattr(uploaded_pdf, "content_type", "") or "")
    if content_type and content_type.lower() not in ALLOWED_PDF_CONTENT_TYPES:
        return Response(
            {"error": "unsupported media type; expected application/pdf."},
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    if not _is_pdf_file(uploaded_pdf):
        return Response(
            {"error": "uploaded file is not a valid pdf."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return uploaded_pdf


def _validate_redaction_payload(request: Request) -> PdfRedactionRequest | Response:
    request_payload = _request_data(request)
    try:
        return PdfRedactionRequest.model_validate(
            {
                "source_type": request_payload.get("source_type", ""),
                "redaction_manifest": request_payload.get("redaction_manifest"),
                "note": request_payload.get("note", ""),
                "client_source_sha256": request_payload.get("client_source_sha256", ""),
            }
        )
    except ValidationError as exc:
        return Response(
            {"error": f"invalid redaction_manifest: {exc}"},
            status=status.HTTP_400_BAD_REQUEST,
        )


def _prepare_redaction(
    pdf: RawPdfFile,
    uploaded_pdf: UploadedFile,
    payload: PdfRedactionRequest,
) -> _PreparedRedaction | Response:
    source_field = (
        _pdf_file(pdf)
        if payload.source_type == PdfProcessingHistory.SOURCE_TYPE_RAW
        else _pdf_processed_file(pdf)
    )
    if not source_field or not getattr(source_field, "name", None):
        return Response(
            {"error": "source file is unavailable for the requested source_type."},
            status=status.HTTP_409_CONFLICT,
        )

    source_sha256 = sha256_file(source_field)
    if payload.client_source_sha256 and payload.client_source_sha256 != source_sha256:
        return Response(
            {
                "error": "source hash mismatch.",
                "expected_source_sha256": source_sha256,
            },
            status=status.HTTP_409_CONFLICT,
        )

    uploaded_sha256 = _sha256_uploaded_file(uploaded_pdf)
    return _PreparedRedaction(
        uploaded_pdf=uploaded_pdf,
        payload=payload,
        manifest=cast(
            dict[str, object],
            payload.redaction_manifest.model_dump(mode="python"),
        ),
        source_sha256=source_sha256,
        processed_filename=(f"{_pdf_hash(pdf)}_redaction_{uploaded_sha256[:12]}.pdf"),
    )


def _persist_redaction(
    request: Request,
    pk: int,
    prepared: _PreparedRedaction,
) -> _PersistedRedaction | Response:
    with transaction.atomic():
        pdf = (
            RawPdfFile.objects.select_related("state")
            .select_for_update()
            .filter(pk=pk)
            .first()
        )
        if pdf is None:
            return Response(
                {"error": f"report with ID {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        state = get_or_create_raw_pdf_state(pdf)
        status_before = _state_status_value(state) or "not_started"

        _save_processed_file(
            pdf,
            filename=prepared.processed_filename,
            uploaded_pdf=prepared.uploaded_pdf,
        )
        _save_pdf_after_processed_file(pdf)

        state_update_fields = [
            "anonymized",
            "anonymization_validated",
            "processed_file_sha256",
            "date_modified",
        ]
        state.anonymized = True
        state.anonymization_validated = False
        state.processed_file_sha256 = ""
        # Keep existing value unless missing; a redacted upload is processed output.
        if not _state_sensitive_meta_processed(state):
            state.sensitive_meta_processed = True
            state_update_fields.append("sensitive_meta_processed")
        _save_pdf_state(state, update_fields=state_update_fields)
        verify_and_persist_processed_report_sha256(pdf)

        user = getattr(request, "user", None)
        actor_user = user if getattr(user, "is_authenticated", False) else None
        history_entry = PdfProcessingHistory.objects.create(
            pdf=pdf,
            operation=PdfProcessingHistory.OPERATION_PDF_REDACTION,
            source_type=prepared.payload.source_type,
            redaction_manifest=prepared.manifest,
            note=prepared.payload.note,
            client_source_sha256=prepared.payload.client_source_sha256,
            source_sha256=prepared.source_sha256,
            processed_file_name=str(getattr(_pdf_processed_file(pdf), "name", "")),
            actor_user=actor_user,
            actor_username=getattr(user, "username", "") if user else "",
            actor_email=getattr(user, "email", "") if user else "",
        )

        pdf.refresh_from_db(fields=["date_modified", "state"])
        status_after = _state_status_value(_pdf_state(pdf)) or status_before

        record_operation(
            _django_request(request),
            action=PdfProcessingHistory.OPERATION_PDF_REDACTION,
            resource_type="pdf",
            resource_id=_pdf_pk(pdf),
            status_before=status_before,
            status_after=status_after,
            meta={
                "revision_id": _history_pk(history_entry),
                "source_type": prepared.payload.source_type,
                "client_source_sha256": (prepared.payload.client_source_sha256 or None),
                "source_sha256": prepared.source_sha256,
            },
        )
    return _PersistedRedaction(pdf=pdf, history_entry=history_entry)


def _redaction_response(persisted: _PersistedRedaction) -> Response:
    pdf = persisted.pdf
    response_payload = PdfRedactionResponse(
        file_id=_pdf_pk(pdf),
        revision_id=_history_pk(persisted.history_entry),
        processed_stream_url=build_pdf_stream_path(_pdf_pk(pdf), file_type="processed"),
        status="done_processing_anonymization",
        anonymization_validated=False,
        updated_at=_pdf_updated_at(pdf),
    )
    return Response(
        response_payload.model_dump(mode="python"),
        status=status.HTTP_201_CREATED,
    )


def _apply_redactions(request: Request, pk: int, pdf: RawPdfFile) -> Response:
    uploaded_pdf = _validate_uploaded_pdf(request)
    if isinstance(uploaded_pdf, Response):
        return uploaded_pdf

    payload = _validate_redaction_payload(request)
    if isinstance(payload, Response):
        return payload

    prepared = _prepare_redaction(pdf, uploaded_pdf, payload)
    if isinstance(prepared, Response):
        return prepared

    persisted = _persist_redaction(request, pk, prepared)
    if isinstance(persisted, Response):
        return persisted
    return _redaction_response(persisted)


class PdfApplyRedactionsView(APIView):
    """
    POST /api/media/pdfs/<pk>/apply-redactions/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request: Request, pk: int) -> Response:
        pdf = RawPdfFile.objects.select_related("state").filter(pk=pk).first()
        if pdf is None:
            return Response(
                {"error": f"report with ID {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        with ProcessingLockContext(pk, file_type="pdf") as lock:
            if not lock.acquired:
                return Response(
                    {"error": "report is currently locked for processing."},
                    status=status.HTTP_409_CONFLICT,
                )

            try:
                return _apply_redactions(request, pk, pdf)
            except Exception:
                logger.exception(
                    "Unexpected failure applying redactions to report %s", pk
                )
                return Response(
                    {"error": "unexpected backend failure while applying redactions."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        return Response(
            {"error": "unexpected processing state while applying redactions."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class PdfProcessingHistoryView(APIView):
    """
    GET /api/media/pdfs/<pk>/processing-history/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request: Request, pk: int) -> Response:
        pdf = RawPdfFile.objects.filter(pk=pk).first()
        if pdf is None:
            return Response(
                {"error": f"report with ID {pk} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        history = PdfProcessingHistory.objects.filter(pdf_id=pdf.pk).order_by(
            "-created_at"
        )
        serializer = PdfProcessingHistorySerializer(
            history,
            many=True,
            context={"request": request},
        )
        return Response(_serializer_data(serializer), status=status.HTTP_200_OK)
