from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, TypeGuard, cast

from django.db.models.fields.files import FieldFile
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework.request import Request
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from lx_dtypes.models.contracts.media_streaming import (
    MediaStreamDisposition,
    MediaStreamFileKind,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.paths import to_storage_relative

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
from endoreg_db.views.access_control import assert_center_scope_allowed


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _field_file_has_name(field_file: object | None) -> TypeGuard[FieldFile]:
    return bool(field_file and isinstance(getattr(field_file, "name", None), str))


def _field_file_name(field_file: object) -> str:
    name = getattr(field_file, "name", None)
    if not isinstance(name, str) or not name:
        raise Http404("Report file is not available")
    return name


def _set_field_file_name(field_file: object, name: str) -> FieldFile:
    field = cast(FieldFile, field_file)
    field.name = name
    return field


def _query_value(
    query_params: "Mapping[str, object]", key: str, default: str = ""
) -> str:
    value = query_params.get(key, default)
    return str(value if value is not None else default)


def _processed_report_fallback_path(report: RawPdfFile) -> Path | None:
    pdf_hash = cast(str, getattr(report, "pdf_hash"))
    candidate = (
        path_utils.EndoregPathsModel.from_environment().anonym_report
        / f"{pdf_hash}.pdf"
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


def _pick_report_field_file(
    report: RawPdfFile, file_type: MediaStreamFileKind
) -> FieldFile:
    if file_type == "processed":
        field_file = getattr(report, "processed_file", None)
        if _field_file_has_name(field_file):
            return field_file

        fallback_path = _processed_report_fallback_path(report)
        if fallback_path is not None:
            relative_name = to_storage_relative(fallback_path)
            return _set_field_file_name(
                getattr(report, "processed_file"),
                relative_name,
            )

        raise Http404("Processed report file not available")

    field_file = getattr(report, "file", None)
    if _field_file_has_name(field_file):
        return field_file

    raw_fallback_path = _raw_report_fallback_path(report)
    if raw_fallback_path is not None and raw_fallback_path.exists():
        relative_name = to_storage_relative(raw_fallback_path)
        return _set_field_file_name(getattr(report, "file"), relative_name)

    raise Http404("Raw report file not available")


def recover_missing_report_field_path(
    report: RawPdfFile, file_type: MediaStreamFileKind
) -> FieldFile | None:
    if file_type == "processed":
        fallback_path = _processed_report_fallback_path(report)
        if fallback_path is not None:
            relative_name = to_storage_relative(fallback_path)
            return _set_field_file_name(
                getattr(report, "processed_file"),
                relative_name,
            )
        return None

    raw_fallback_path = _raw_report_fallback_path(report)
    if raw_fallback_path is not None and raw_fallback_path.exists():
        relative_name = to_storage_relative(raw_fallback_path)
        return _set_field_file_name(getattr(report, "file"), relative_name)
    return None


def _resolve_local_path_for_nginx(field_file: object) -> Path | None:
    return maybe_local_plaintext_path(field_file)


def _serve_with_nginx(
    field_file: object,
    content_type: str,
    *,
    disposition: MediaStreamDisposition,
    frontend_origin: str | None,
) -> HttpResponseBase | None:
    path = _resolve_local_path_for_nginx(field_file)
    if path is None:
        return None

    try:
        return build_nginx_accel_response_for_path(
            path=path,
            content_type=content_type,
            filename=Path(_field_file_name(field_file)).name,
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


def build_eager_content_response(
    *,
    field_file: object,
    content_type: str,
    file_size: int,
    range_header: str | None,
    disposition: MediaStreamDisposition,
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


class _RemotePathIterator:
    def __init__(
        self,
        *,
        manager: object,
        path: Path,
        start: int,
        length: int,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        self._manager = manager
        self._handle: BinaryIO | None = path.open("rb")
        self._handle.seek(start)
        self._remaining = length
        self._chunk_size = chunk_size
        self._closed = False

    def __iter__(self) -> "_RemotePathIterator":
        return self

    def __next__(self) -> bytes:
        if self._closed or self._remaining == 0:
            self.close()
            raise StopIteration
        assert self._handle is not None
        chunk = self._handle.read(min(self._chunk_size, self._remaining))
        if not chunk:
            self.close()
            raise IOError("remote processed report ended before expected size")
        self._remaining -= len(chunk)
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        cast(Any, self._manager).__exit__(None, None, None)


def _serve_remote_processed_report(
    *,
    report_id: int,
    range_header: str | None,
    disposition: MediaStreamDisposition,
    frontend_origin: str | None,
) -> HttpResponseBase:
    from endoreg_db.services.hub.remote_processed_report import (
        materialize_remote_processed_report,
    )

    manager = materialize_remote_processed_report(report_id=report_id)
    try:
        path = manager.__enter__()
    except Exception:
        raise
    try:
        file_size = path.stat().st_size
        if file_size <= 0:
            raise FileNotFoundError("remote processed report is empty")
        if range_header:
            try:
                byte_range = parse_byte_range(range_header, file_size)
            except ValueError:
                manager.__exit__(None, None, None)
                response = HttpResponse(status=416, content_type="application/pdf")
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return _add_cors_headers_if_configured(response, frontend_origin)
            start = byte_range.start
            length = byte_range.length
            status_code = 206
        else:
            start = 0
            length = file_size
            status_code = 200
    except Exception:
        manager.__exit__(None, None, None)
        raise

    try:
        iterator = _RemotePathIterator(
            manager=manager,
            path=path,
            start=start,
            length=length,
        )
    except Exception:
        manager.__exit__(None, None, None)
        raise
    response = StreamingHttpResponse(
        iterator,
        status=status_code,
        content_type="application/pdf",
    )
    if status_code == 206:
        response["Content-Range"] = f"bytes {start}-{start + length - 1}/{file_size}"
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = (
        f'{disposition}; filename="processed-report-{report_id}.pdf"'
    )
    return _add_cors_headers_if_configured(response, frontend_origin)


@method_decorator(xframe_options_exempt, name="dispatch")
class ReportStreamView(APIView):
    permission_classes = [EnvironmentAwarePermission, PolicyPermission]

    @staticmethod
    def _parse_file_type(request: Request) -> MediaStreamFileKind:
        type_value = request.query_params.get("type")
        file_type_value = request.query_params.get("file_type")
        raw_value = str(type_value if type_value is not None else file_type_value)
        return "processed" if raw_value.lower() == "processed" else "raw"

    @staticmethod
    def _parse_disposition(request: Request) -> MediaStreamDisposition:
        download_raw = _query_value(request.query_params, "download").lower()
        return "attachment" if download_raw in {"1", "true", "yes", "on"} else "inline"

    @staticmethod
    def _range_header(request: Request) -> str | None:
        header_value = request.headers.get("Range")
        if header_value is not None:
            return header_value
        meta_value = request.META.get("HTTP_RANGE")
        if isinstance(meta_value, str):
            return meta_value
        return None

    def get(
        self,
        request: Request,
        pk: int | str,
        *args: object,
        **kwargs: object,
    ) -> HttpResponseBase:
        try:
            report_id = int(pk)
        except (TypeError, ValueError):
            raise Http404("Invalid report ID format")

        try:
            report = RawPdfFile.objects.get(pk=report_id)
        except RawPdfFile.DoesNotExist as exc:
            raise Http404(f"Report with ID {pk} not found") from exc

        assert_center_scope_allowed(request=request, obj=report)
        self.check_object_permissions(request, report)

        file_type = self._parse_file_type(request)

        disposition = self._parse_disposition(request)
        frontend_origin = resolve_response_origin(request)
        range_header = self._range_header(request)
        try:
            field_file = _pick_report_field_file(report, file_type)
        except Http404:
            if file_type != "processed":
                raise
            try:
                return _serve_remote_processed_report(
                    report_id=report_id,
                    range_header=range_header,
                    disposition=disposition,
                    frontend_origin=frontend_origin,
                )
            except Exception as exc:
                logger.warning(
                    "Remote processed report is unavailable: id=%s error=%s",
                    report_id,
                    type(exc).__name__,
                )
                raise Http404("Report file is not available") from exc
        if field_file_is_local_encrypted_without_reader(field_file):
            logger.error(
                "Refusing to stream encrypted report bytes without a decrypting "
                "storage backend: id=%s type=%s path=%s",
                report_id,
                file_type,
                getattr(field_file, "name", None),
            )
            raise Http404("Report file is not available")
        field_file_name = _field_file_name(field_file)
        filename = Path(field_file_name).name
        content_type = mimetypes.guess_type(field_file_name)[0] or "application/pdf"
        recovered_from_fallback = False
        try:
            file_size = field_file_size(field_file)
        except FileNotFoundError as exc:
            recovered_field_file = recover_missing_report_field_path(report, file_type)
            if recovered_field_file is None:
                if file_type == "processed":
                    try:
                        return _serve_remote_processed_report(
                            report_id=report_id,
                            range_header=range_header,
                            disposition=disposition,
                            frontend_origin=frontend_origin,
                        )
                    except Exception as remote_exc:
                        logger.warning(
                            "Remote processed report is unavailable: id=%s error=%s",
                            report_id,
                            type(remote_exc).__name__,
                        )
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
            streaming_response = build_eager_content_response(
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
