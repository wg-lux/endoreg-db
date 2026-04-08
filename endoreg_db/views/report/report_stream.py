from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from django.http import Http404, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework.views import APIView

from endoreg_db.authz.permissions import PolicyPermission
from endoreg_db.models import RawPdfFile
from endoreg_db.utils.permissions import EnvironmentAwarePermission
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.views.access_control import assert_center_scope_allowed

from ..media.storage_streaming import (
    add_cors_headers,
    build_partial_content_response,
    field_file_size,
    maybe_local_plaintext_path,
    parse_byte_range,
)

logger = logging.getLogger(__name__)
NGINX_PROTECTED_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


def _pick_report_field_file(report: RawPdfFile, file_type: str):
    if file_type == "processed":
        field_file = getattr(report, "processed_file", None)
        if not field_file or not getattr(field_file, "name", None):
            raise Http404("Processed report file not available")
        return field_file

    field_file = getattr(report, "file", None)
    if not field_file or not getattr(field_file, "name", None):
        raise Http404("Raw report file not available")
    return field_file


def _serve_with_nginx(
    field_file, content_type: str, *, disposition: str
) -> HttpResponse | None:
    path = maybe_local_plaintext_path(field_file)
    if path is None:
        return None

    try:
        relative_path = path.relative_to(STORAGE_DIR.resolve())
    except ValueError:
        logger.warning(
            "Report file %s is outside STORAGE_DIR %s. Falling back to Django streaming.",
            path,
            STORAGE_DIR,
        )
        return None

    response = HttpResponse()
    response["Content-Type"] = content_type
    response["X-Accel-Redirect"] = os.path.join(NGINX_PROTECTED_URL, str(relative_path))
    response["X-Accel-Buffering"] = "no"
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = (
        f'{disposition}; filename="{Path(field_file.name).name}"'
    )
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
        assert_center_scope_allowed(
            request=request,
            obj=report,
            not_found_message=f"Report with ID {pk} not found",
        )

        file_type = str(
            request.query_params.get("type")
            or request.query_params.get("file_type")
            or "raw"
        ).lower()
        if file_type not in {"raw", "processed"}:
            file_type = "raw"

        field_file = _pick_report_field_file(report, file_type)
        filename = Path(field_file.name).name
        content_type = mimetypes.guess_type(field_file.name)[0] or "application/pdf"
        file_size = field_file_size(field_file)
        if file_size <= 0:
            raise Http404("Report file is empty")

        download_raw = str(request.query_params.get("download", "")).lower()
        disposition = (
            "attachment" if download_raw in {"1", "true", "yes", "on"} else "inline"
        )
        frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")
        range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE")
        serve_with_nginx = os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"

        if serve_with_nginx and not range_header:
            nginx_response = _serve_with_nginx(
                field_file,
                content_type,
                disposition=disposition,
            )
            if nginx_response is not None:
                return add_cors_headers(nginx_response, frontend_origin)

        if range_header:
            try:
                parse_byte_range(range_header, file_size)
            except ValueError:
                response = HttpResponse(status=416, content_type=content_type)
                response["Content-Range"] = f"bytes */{file_size}"
                response["Accept-Ranges"] = "bytes"
                return add_cors_headers(response, frontend_origin)

        streaming_response = build_partial_content_response(
            field_file=field_file,
            content_type=content_type,
            file_size=file_size,
            range_header=range_header,
            disposition=disposition,
            filename=filename,
        )
        return add_cors_headers(streaming_response, frontend_origin)
