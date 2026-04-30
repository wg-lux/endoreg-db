from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.http import Http404, HttpResponse, HttpResponseBase, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework.views import APIView
from typing import TYPE_CHECKING

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import RawPdfFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.paths import to_storage_relative
from endoreg_db.utils.paths import ANONYM_REPORT_DIR

from endoreg_db.utils.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    field_file_is_local_encrypted_without_reader,
    field_file_size,
    iter_field_file_bytes,
    maybe_local_plaintext_path,
    parse_byte_range,
)

from endoreg_db.utils.nginx_accel import (
    build_nginx_accel_response_for_path,
    nginx_offload_enabled,
)

from endoreg_db.utils.cors import resolve_response_origin


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from django.db.models.fields.files import FieldFile


def _processed_report_fallback_path(report: RawPdfFile) -> Path | None:
    candidate = (
        path_utils.EndoregPathsModel.from_environment().anonym_report
        / f"{report.pdf_hash}.pdf"
    )
    return path_utils.resolve_existing_protected_media_path(candidate)


def _raw_report_fallback_path(report: RawPdfFile) -> Path | None:
    pdf_hash = getattr(report, "pdf_hash", None)
    if not pdf_hash:
        return None

    candidates = (
        path_utils.EndoregPathsModel.from_environment().sensitive_report
        / f"{pdf_hash}.pdf",
        path_utils.EndoregPathsModel.from_environment().import_report
        / f"{pdf_hash}.pdf",
    )
    for candidate in candidates:
        resolved = path_utils.resolve_existing_protected_media_path(candidate)
        if resolved is not None:
            return resolved
    return None


def _pick_report_field_file(report: RawPdfFile, file_type: str):
    if file_type == "processed":
        field_file = getattr(report, "processed_file", None)
        if field_file and getattr(field_file, "name", None):
            return field_file

        fallback_path = _processed_report_fallback_path(report)
        if fallback_path is not None:
            relative_name = to_storage_relative(fallback_path)
            report.processed_file.name = relative_name
            return report.processed_file

        raise Http404("Processed report file not available")

    field_file = getattr(report, "file", None)
    if field_file and getattr(field_file, "name", None):
        return field_file

    raw_fallback_path = _raw_report_fallback_path(report)
    if raw_fallback_path is not None and raw_fallback_path.exists():
        relative_name = to_storage_relative(raw_fallback_path)
        report.file.name = relative_name
        return report.file

    raise Http404("Raw report file not available")


def _recover_missing_report_field_path(
    report: RawPdfFile, file_type: str
) -> "FieldFile | None":
    if file_type == "processed":
        fallback_path = _processed_report_fallback_path(report)
        if fallback_path is not None:
            relative_name = to_storage_relative(fallback_path)
            report.processed_file.name = relative_name
            return report.processed_file
        return None

    raw_fallback_path = _raw_report_fallback_path(report)
    if raw_fallback_path is not None and raw_fallback_path.exists():
        relative_name = to_storage_relative(raw_fallback_path)
        report.file.name = relative_name
        return report.file
    return None


def _resolve_local_path_for_nginx(field_file) -> Path | None:
    return maybe_local_plaintext_path(field_file)


def _serve_with_nginx(
    field_file,
    content_type: str,
    *,
    disposition: str,
    frontend_origin: str | None,
) -> HttpResponseBase | None:
    path = _resolve_local_path_for_nginx(field_file)
    if path is None:
        return None

    try:
        return build_nginx_accel_response_for_path(
            path=path,
            content_type=content_type,
            filename=Path(field_file.name).name,
            disposition=disposition,
            frontend_origin=frontend_origin,
        )
    except ValueError:
        logger.warning(
            "Report file %s is outside the configured protected media root. Falling back to Django streaming.",
            path,
        )
        return None


def _add_cors_headers_if_configured(
    response: HttpResponseBase, frontend_origin: str | None
) -> HttpResponseBase:
    if frontend_origin is None:
        return response
    return add_cors_headers(response, frontend_origin)


def _build_eager_content_response(
    *,
    field_file,
    content_type: str,
    file_size: int,
    range_header: str | None,
    disposition: str,
    filename: str,
) -> StreamingHttpResponse:
    if range_header:
        byte_range = parse_byte_range(range_header, file_size)
        payload = b"".join(
            iter_field_file_bytes(
                field_file,
                start=byte_range.start,
                end=byte_range.end,
            )
        )
        response = StreamingHttpResponse(
            iter((payload,)),
            status=206,
            content_type=content_type,
        )
        response["Content-Range"] = (
            f"bytes {byte_range.start}-{byte_range.end}/{file_size}"
        )
        response["Content-Length"] = str(byte_range.length)
    else:
        payload = b"".join(
            iter_field_file_bytes(field_file, start=0, end=file_size - 1)
        )
        response = StreamingHttpResponse(
            iter((payload,)),
            status=200,
            content_type=content_type,
        )
        response["Content-Length"] = str(file_size)

    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


@method_decorator(xframe_options_exempt, name="dispatch")
class ReportStreamView(APIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    def get(self, request, pk: int, *args, **kwargs):
        try:
            report_id = int(pk)
        except (TypeError, ValueError):
            raise Http404("Invalid report ID format")

        try:
            report = RawPdfFile.objects.get(pk=report_id)
        except RawPdfFile.DoesNotExist as exc:
            raise Http404(f"Report with ID {pk} not found") from exc

        self.check_object_permissions(request, report)

        file_type = str(
            request.query_params.get("type")
            or request.query_params.get("file_type")
            or "raw"
        ).lower()
        if file_type not in {"raw", "processed"}:
            file_type = "raw"

        field_file = _pick_report_field_file(report, file_type)
        if field_file_is_local_encrypted_without_reader(field_file):
            logger.error(
                "Refusing to stream encrypted report bytes without a decrypting "
                "storage backend: id=%s type=%s path=%s",
                report_id,
                file_type,
                getattr(field_file, "name", None),
            )
            raise Http404("Report file is not available")
        filename = Path(field_file.name).name
        content_type = mimetypes.guess_type(field_file.name)[0] or "application/pdf"
        recovered_from_fallback = False
        try:
            file_size = field_file_size(field_file)
        except FileNotFoundError as exc:
            recovered_field_file = _recover_missing_report_field_path(report, file_type)
            if recovered_field_file is None:
                logger.warning(
                    "Report stream file missing for id=%s type=%s path=%s: %s",
                    report_id,
                    file_type,
                    getattr(field_file, "name", None),
                    exc,
                )
                raise Http404("Report file is not available") from exc
            field_file = recovered_field_file
            recovered_from_fallback = True
            file_size = field_file_size(field_file)
        if file_size <= 0:
            raise Http404("Report file is empty")

        download_raw = str(request.query_params.get("download", "")).lower()
        disposition = (
            "attachment" if download_raw in {"1", "true", "yes", "on"} else "inline"
        )
        frontend_origin = resolve_response_origin(request)
        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")

        if nginx_offload_enabled() and not range_header and not recovered_from_fallback:
            nginx_response = _serve_with_nginx(
                field_file,
                content_type,
                disposition=disposition,
                frontend_origin=frontend_origin,
            )
            if nginx_response is not None:
                return nginx_response

        if range_header:
            try:
                parse_byte_range(range_header, file_size)
            except ValueError:
                response = HttpResponse(status=416, content_type=content_type)
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return _add_cors_headers_if_configured(response, frontend_origin)

        if recovered_from_fallback:
            streaming_response = _build_eager_content_response(
                field_file=field_file,
                content_type=content_type,
                file_size=file_size,
                range_header=range_header,
                disposition=disposition,
                filename=filename,
            )
        else:
            streaming_response = build_partial_content_response(
                field_file=field_file,
                content_type=content_type,
                file_size=file_size,
                range_header=range_header,
                disposition=disposition,
                filename=filename,
            )
        return _add_cors_headers_if_configured(streaming_response, frontend_origin)
