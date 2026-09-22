from __future__ import annotations

import logging
import mimetypes
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from endoreg_db.utils.storage.report_fields import ReportArtifactFieldFile
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework.request import Request

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.openapi import OpenApiAPIView as APIView
from endoreg_db.utils.cors import resolve_response_origin
from endoreg_db.utils.nginx_accel import (
    build_nginx_accel_response_for_path,
    nginx_offload_enabled,
)
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    field_file_is_local_encrypted_without_reader,
    field_file_has_decrypting_storage,
    field_file_size,
    parse_byte_range,
)
from endoreg_db.views.access_control import assert_center_scope_allowed
from lx_dtypes.models.contracts.media_streaming import (
    MediaStreamDisposition,
    MediaStreamFileKind,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _query_value(
    query_params: "Mapping[str, object]", key: str, default: str = ""
) -> str:
    value = query_params.get(key, default)
    return str(value if value is not None else default)


def _pick_report_field_file(
    report: RawPdfFile, file_type: MediaStreamFileKind
) -> ReportArtifactFieldFile:
    field_file: ReportArtifactFieldFile | None = (
        report.processed_file if file_type == "processed" else report.file
    )
    if not field_file or not field_file.name:
        raise Http404("Report file is not available")
    return field_file


def _serve_with_nginx(
    field_file: ReportArtifactFieldFile,
    local_path: Path | None,
    content_type: str,
    *,
    disposition: MediaStreamDisposition,
    frontend_origin: str | None,
) -> HttpResponse | None:
    if local_path is None or field_file_has_decrypting_storage(field_file):
        return None

    try:
        return build_nginx_accel_response_for_path(
            path=local_path,
            content_type=content_type,
            filename=field_file.name,
            disposition=disposition,
            frontend_origin=frontend_origin,
        )
    except ValueError:
        logger.warning(
            "Report file %s is outside the configured protected media root. Falling back to Django streaming.",
            local_path,
        )
        return None


def _add_cors_headers_if_configured[_ResponseT: HttpResponse | StreamingHttpResponse](
    response: _ResponseT, frontend_origin: str | None
) -> _ResponseT:
    if frontend_origin is None:
        return response
    return add_cors_headers(response, frontend_origin)


build_eager_content_response = build_partial_content_response


class _RemotePathIterator:
    def __init__(
        self,
        *,
        manager: ExitStack,
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
        self._manager.close()


def _serve_remote_processed_report(
    *,
    report_id: int,
    range_header: str | None,
    disposition: MediaStreamDisposition,
    frontend_origin: str | None,
) -> HttpResponse | StreamingHttpResponse:
    from endoreg_db.services.hub.remote_processed_report import (
        materialize_remote_processed_report,
    )

    with ExitStack() as stack:
        path = stack.enter_context(
            materialize_remote_processed_report(report_id=report_id)
        )
        file_size = path.stat().st_size
        if file_size <= 0:
            raise FileNotFoundError("remote processed report is empty")
        if range_header:
            try:
                byte_range = parse_byte_range(range_header, file_size)
            except ValueError:
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
        manager = stack.pop_all()

    try:
        iterator = _RemotePathIterator(
            manager=manager,
            path=path,
            start=start,
            length=length,
        )
    except OSError:
        manager.close()
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
    ) -> HttpResponse | StreamingHttpResponse:
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
                field_file.name,
            )
            raise Http404("Report file is not available")
        if not field_file.name:
            raise Http404("Report file is not available")
        filename = Path(field_file.name).name
        content_type = mimetypes.guess_type(field_file.name)[0] or "application/pdf"

        try:
            file_size = field_file_size(field_file)
        except FileNotFoundError as exc:
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
                field_file.name,
                exc,
            )
            raise Http404("Report file is not available") from exc

        if file_size <= 0:
            raise Http404("Report file is empty")

        if nginx_offload_enabled() and not range_header:
            local_path = field_file.local_plaintext_path()
            nginx_response = _serve_with_nginx(
                field_file,
                local_path,
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

        streaming_response = build_partial_content_response(
            field_file=field_file,
            content_type=content_type,
            file_size=file_size,
            range_header=range_header,
            disposition=disposition,
            filename=filename,
        )
        return _add_cors_headers_if_configured(streaming_response, frontend_origin)
