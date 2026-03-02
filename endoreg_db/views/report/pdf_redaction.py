import hashlib
import json
import logging
from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import PdfProcessingHistory, RawPdfFile
from endoreg_db.serializers.pdf.pdf_processing_history import (
    PdfProcessingHistorySerializer,
)
from endoreg_db.services.polling_coordinator import ProcessingLockContext
from endoreg_db.utils.file_operations import sha256_file
from endoreg_db.utils.operation_log import record_operation
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage import ensure_local_file

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


def _parse_manifest(raw_manifest: Any) -> dict[str, Any]:
    if isinstance(raw_manifest, dict):
        return raw_manifest
    if isinstance(raw_manifest, str):
        payload = raw_manifest.strip()
        if not payload:
            raise ValueError("redaction_manifest must not be empty.")
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise ValueError("redaction_manifest must be a JSON object.")
        return parsed
    raise ValueError("redaction_manifest must be a JSON string.")


def _validate_manifest(manifest: dict[str, Any]) -> str | None:
    version = manifest.get("version")
    if not isinstance(version, int) or version < 1:
        return "redaction_manifest.version must be an integer >= 1."

    normalized = manifest.get("normalized")
    if normalized is not True:
        return "redaction_manifest.normalized must be true."

    pages = manifest.get("pages")
    if not isinstance(pages, list):
        return "redaction_manifest.pages must be a list."

    for page_index, page_entry in enumerate(pages):
        if not isinstance(page_entry, dict):
            return f"redaction_manifest.pages[{page_index}] must be an object."

        page_number = page_entry.get("page")
        if not isinstance(page_number, int) or page_number < 1:
            return (
                f"redaction_manifest.pages[{page_index}].page must be an integer >= 1."
            )

        boxes = page_entry.get("boxes")
        if not isinstance(boxes, list):
            return f"redaction_manifest.pages[{page_index}].boxes must be a list."

        for box_index, box in enumerate(boxes):
            if not isinstance(box, dict):
                return (
                    f"redaction_manifest.pages[{page_index}].boxes[{box_index}] "
                    "must be an object."
                )

            for key in ("x", "y", "width", "height"):
                value = box.get(key)
                if not isinstance(value, (int, float)):
                    return (
                        f"redaction_manifest.pages[{page_index}].boxes[{box_index}].{key} "
                        "must be numeric."
                    )
                if value < 0 or value > 1:
                    return (
                        f"redaction_manifest.pages[{page_index}].boxes[{box_index}].{key} "
                        "must be within [0, 1]."
                    )

            width = float(box["width"])
            height = float(box["height"])
            x_coord = float(box["x"])
            y_coord = float(box["y"])
            if width <= 0 or height <= 0:
                return (
                    f"redaction_manifest.pages[{page_index}].boxes[{box_index}] "
                    "width and height must be > 0."
                )
            if x_coord + width > 1 or y_coord + height > 1:
                return (
                    f"redaction_manifest.pages[{page_index}].boxes[{box_index}] "
                    "must fit inside normalized page bounds."
                )

    return None


def _is_pdf_file(uploaded_file) -> bool:
    try:
        uploaded_file.seek(0)
        header = uploaded_file.read(len(PDF_MAGIC_HEADER))
    finally:
        uploaded_file.seek(0)
    return header == PDF_MAGIC_HEADER


def _sha256_uploaded_file(uploaded_file) -> str:
    digest = hashlib.sha256()
    try:
        uploaded_file.seek(0)
        for chunk in uploaded_file.chunks():
            digest.update(chunk)
    finally:
        uploaded_file.seek(0)
    return digest.hexdigest()


def _sha256_field_file(field_file) -> str | None:
    if not field_file or not getattr(field_file, "name", ""):
        return None
    try:
        with ensure_local_file(field_file) as local_path:
            return sha256_file(local_path)
    except Exception:
        logger.exception("Failed to compute source sha256 for %s", field_file.name)
        return None


class PdfApplyRedactionsView(APIView):
    """
    POST /api/media/pdfs/<pk>/apply-redactions/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk: int):
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
                uploaded_pdf = request.FILES.get("file")
                if uploaded_pdf is None:
                    return Response(
                        {"error": "file is required."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                file_size = getattr(uploaded_pdf, "size", 0)
                if (
                    isinstance(file_size, int)
                    and file_size > MAX_REDACTION_UPLOAD_BYTES
                ):
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
                if (
                    content_type
                    and content_type.lower() not in ALLOWED_PDF_CONTENT_TYPES
                ):
                    return Response(
                        {"error": "unsupported media type; expected application/pdf."},
                        status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    )

                if not _is_pdf_file(uploaded_pdf):
                    return Response(
                        {"error": "uploaded file is not a valid pdf."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                source_type = str(request.data.get("source_type", "")).strip().lower()
                if source_type not in {
                    PdfProcessingHistory.SOURCE_TYPE_RAW,
                    PdfProcessingHistory.SOURCE_TYPE_PROCESSED,
                }:
                    return Response(
                        {"error": "source_type must be either 'raw' or 'processed'."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                try:
                    manifest = _parse_manifest(request.data.get("redaction_manifest"))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    return Response(
                        {"error": f"invalid redaction_manifest: {exc}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                manifest_error = _validate_manifest(manifest)
                if manifest_error is not None:
                    return Response(
                        {"error": manifest_error},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                note_value = request.data.get("note", "")
                note = str(note_value).strip() if note_value is not None else ""

                client_source_sha256_raw = request.data.get("client_source_sha256", "")
                client_source_sha256 = (
                    str(client_source_sha256_raw).strip().lower()
                    if client_source_sha256_raw is not None
                    else ""
                )
                if client_source_sha256:
                    if len(client_source_sha256) != 64:
                        return Response(
                            {
                                "error": "client_source_sha256 must contain 64 hex chars."
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    if any(ch not in "0123456789abcdef" for ch in client_source_sha256):
                        return Response(
                            {"error": "client_source_sha256 must be lowercase hex."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                source_field = (
                    pdf.file
                    if source_type == PdfProcessingHistory.SOURCE_TYPE_RAW
                    else pdf.processed_file
                )
                source_sha256 = _sha256_field_file(source_field)
                if source_sha256 is None:
                    return Response(
                        {
                            "error": "source file is unavailable for the requested source_type."
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                if client_source_sha256 and client_source_sha256 != source_sha256:
                    return Response(
                        {
                            "error": "source hash mismatch.",
                            "expected_source_sha256": source_sha256,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )

                uploaded_sha256 = _sha256_uploaded_file(uploaded_pdf)
                processed_filename = (
                    f"{pdf.pdf_hash}_redaction_{uploaded_sha256[:12]}.pdf"
                )

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

                    state = pdf.get_or_create_state()
                    status_before = _state_status_value(state) or "not_started"

                    pdf.processed_file.save(
                        processed_filename, uploaded_pdf, save=False
                    )
                    pdf.save(update_fields=["processed_file", "date_modified"])

                    state_update_fields = [
                        "anonymized",
                        "anonymization_validated",
                        "date_modified",
                    ]
                    state.anonymized = True
                    state.anonymization_validated = False
                    # Keep existing value unless missing; a redacted upload is processed output.
                    if not state.sensitive_meta_processed:
                        state.sensitive_meta_processed = True
                        state_update_fields.append("sensitive_meta_processed")
                    state.save(update_fields=state_update_fields)

                    user = getattr(request, "user", None)
                    actor_user = (
                        user if getattr(user, "is_authenticated", False) else None
                    )
                    history_entry = PdfProcessingHistory.objects.create(
                        pdf=pdf,
                        operation=PdfProcessingHistory.OPERATION_PDF_REDACTION,
                        source_type=source_type,
                        redaction_manifest=manifest,
                        note=note,
                        client_source_sha256=client_source_sha256,
                        source_sha256=source_sha256,
                        processed_file_name=str(
                            getattr(pdf.processed_file, "name", "")
                        ),
                        actor_user=actor_user,
                        actor_username=getattr(user, "username", "") if user else "",
                        actor_email=getattr(user, "email", "") if user else "",
                    )

                    pdf.refresh_from_db(fields=["date_modified", "state"])
                    status_after = _state_status_value(pdf.state) or status_before

                    record_operation(
                        request,
                        action=PdfProcessingHistory.OPERATION_PDF_REDACTION,
                        resource_type="pdf",
                        resource_id=pdf.pk,
                        status_before=status_before,
                        status_after=status_after,
                        meta={
                            "revision_id": history_entry.pk,
                            "source_type": source_type,
                            "client_source_sha256": client_source_sha256 or None,
                            "source_sha256": source_sha256,
                        },
                    )

                return Response(
                    {
                        "file_id": pdf.pk,
                        "revision_id": history_entry.pk,
                        "processed_stream_url": (
                            f"/api/media/pdfs/{pdf.pk}/stream/?type=processed"
                        ),
                        "status": "done_processing_anonymization",
                        "anonymization_validated": False,
                        "updated_at": pdf.date_modified.isoformat(),
                    },
                    status=status.HTTP_201_CREATED,
                )

            except Exception:
                logger.exception(
                    "Unexpected failure applying redactions to report %s", pk
                )
                return Response(
                    {"error": "unexpected backend failure while applying redactions."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )


class PdfProcessingHistoryView(APIView):
    """
    GET /api/media/pdfs/<pk>/processing-history/
    """

    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request, pk: int):
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
        return Response(serializer.data, status=status.HTTP_200_OK)
