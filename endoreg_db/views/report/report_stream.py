import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Optional

from django.http import FileResponse, Http404, StreamingHttpResponse, HttpResponse
from django.views.decorators.clickjacking import (
    xframe_options_exempt,
)
from rest_framework.views import APIView

from endoreg_db.models import RawPdfFile
from endoreg_db.utils.paths import STORAGE_DIR

from ...utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")
NGINX_PROTECTED_URL = os.environ.get("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")


class ClosingFileWrapper:
    """Custom file wrapper that ensures file is closed after streaming"""

    def __init__(self, file_handle, blksize=8192):
        self.file_handle = file_handle
        self.blksize = blksize

    def __iter__(self):
        return self

    def __next__(self):
        data = self.file_handle.read(self.blksize)
        if not data:
            self.file_handle.close()
            raise StopIteration
        return data

    def close(self):
        if hasattr(self.file_handle, "close"):
            self.file_handle.close()


def _get_report_path(pdf_obj: RawPdfFile, file_type: str) -> Path:
    if file_type == "raw":
        file_field = pdf_obj.file
        if not file_field:
            raise Http404("Raw report file not available")
    elif file_type == "processed":
        file_field = pdf_obj.processed_file
        if not file_field:
            raise Http404("Processed report file not available")
    else:
        raise Http404("Invalid file type")

    file_name = getattr(file_field, "name", None)
    if not file_name:
        raise Http404("Report file reference is missing")

    # Canonical path resolution: prefer the storage-backed FieldFile.path.
    try:
        path = Path(file_field.path)
        if path.exists():
            return path.resolve()
        logger.warning(
            "Resolved %s report path via file_field.path but file is missing: %s",
            file_type,
            path,
        )
    except (NotImplementedError, AttributeError, ValueError, OSError) as exc:
        logger.debug(
            "Could not resolve %s report via file_field.path for %s: %s",
            file_type,
            file_name,
            exc,
        )

    # Fallback: resolve from stored name (absolute or relative to STORAGE_DIR).
    file_name_str = str(file_name)
    if file_name_str.startswith("/"):
        path = Path(file_name_str)
    else:
        path = STORAGE_DIR / file_name_str

    if not path.exists():
        try:
            exists_in_storage = file_field.storage.exists(file_name_str)
        except Exception as exc:
            logger.warning(
                "Failed checking storage existence for %s report %s: %s",
                file_type,
                file_name_str,
                exc,
            )
            exists_in_storage = False

        if exists_in_storage:
            logger.warning(
                "%s report exists in storage backend but not as local path: %s",
                file_type.capitalize(),
                file_name_str,
            )

        raise Http404(f"Report file not found on disk: {path}")
    return path.resolve()


def _serve_with_nginx(
    path: Path, content_type: str, *, as_download: bool, filename: str
) -> Optional[HttpResponse]:
    try:
        relative_path = path.resolve().relative_to(STORAGE_DIR.resolve())
    except ValueError:
        logger.warning(
            "Report file %s is outside STORAGE_DIR %s; falling back to Python streaming",
            path,
            STORAGE_DIR,
        )
        return None

    redirect_url = os.path.join(NGINX_PROTECTED_URL, str(relative_path))
    response = HttpResponse()
    response["Content-Type"] = content_type
    response["X-Accel-Redirect"] = redirect_url
    response["X-Accel-Buffering"] = "no"
    disposition = "attachment" if as_download else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    logger.info("Offloading report to Nginx: %s", redirect_url)
    return response


class ReportStreamView(APIView):
    """
    Streams a report file with correct HTTP range support and proper file handle management.

    Supports streaming both raw (original) and processed report files.

    Query Parameters:
        type: 'raw' (default) or 'processed' - Selects which report file to stream
        download: '1'|'true' to force attachment download (default is inline)

    Examples:
        GET /api/media/pdf/1/?type=raw - Stream original raw report
        GET /api/media/pdf/1/?type=processed - Stream processed report
    """

    permission_classes = [EnvironmentAwarePermission]

    @xframe_options_exempt
    def get(self, request, pk: int, *args, **kwargs):
        file_type = "raw"  # Initialize for error logging
        try:
            pdf_obj = RawPdfFile.objects.filter(pk=pk).first()
            if not pdf_obj:
                logger.warning(f"report not found: ID {pk}")
                raise Http404("report not found")

            # Parse query parameters to determine which file to stream
            file_type = (
                request.query_params.get("type")
                or request.query_params.get("file_type")
                or "raw"
            ).lower()
            if file_type not in ["raw", "processed"]:
                logger.warning(f"Invalid file_type '{file_type}', defaulting to 'raw'")
                file_type = "raw"

            # Resolve filesystem path and derive field for metadata/filename
            if file_type == "raw":
                file_field = pdf_obj.file
            else:
                file_field = pdf_obj.processed_file
            if not file_field:
                raise Http404(f"{file_type.capitalize()} report file not available")

            try:
                path = _get_report_path(pdf_obj, file_type)
                file_size = path.stat().st_size
                if file_size <= 0:
                    raise Http404("Report file is empty")
            except (OSError, IOError, AttributeError) as e:
                logger.error(f"Error accessing {file_type} report file {pk}: {e}")
                raise Http404(f"{file_type.capitalize()} report file not accessible")

            # Generate safe filename
            base_filename = (
                os.path.basename(file_field.name)
                if file_field.name
                else f"document_{pk}.pdf"
            )
            if not base_filename.endswith(".pdf"):
                base_filename += ".pdf"

            # Add type indicator to filename for clarity
            if file_type == "processed":
                name_parts = base_filename.rsplit(".", 1)
                safe_filename = f"{name_parts[0]}_processed.{name_parts[1]}"
            else:
                safe_filename = base_filename

            download_raw = str(request.query_params.get("download", "")).lower()
            as_download = download_raw in {"1", "true", "yes", "on"}
            content_disposition = "attachment" if as_download else "inline"
            frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://localhost:8000")

            mime, _ = mimetypes.guess_type(str(path))
            content_type = mime or "application/pdf"

            # Production path: nginx offload (full response only, no Python range handling)
            serve_with_nginx = (
                os.environ.get("SERVE_WITH_NGINX", "false").lower() == "true"
            )
            range_header = request.headers.get("Range") or request.META.get(
                "HTTP_RANGE"
            )
            if serve_with_nginx and not range_header:
                nginx_response = _serve_with_nginx(
                    path,
                    content_type,
                    as_download=as_download,
                    filename=safe_filename,
                )
                if nginx_response is not None:
                    nginx_response["Access-Control-Allow-Origin"] = frontend_origin
                    nginx_response["Access-Control-Allow-Credentials"] = "true"
                    nginx_response["Accept-Ranges"] = "bytes"
                    return nginx_response

            # Handle Range requests
            if range_header:
                logger.debug(
                    f"Range request for {file_type} report {pk}: {range_header}"
                )
                match = _RANGE_RE.match(range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2) or file_size - 1)

                    # Validate range
                    if start >= file_size or start < 0:
                        logger.warning(
                            f"Invalid range start {start} for file size {file_size}"
                        )
                        raise Http404("Invalid range")

                    if end >= file_size:
                        end = file_size - 1

                    chunk_size = end - start + 1

                    try:
                        file_handle = open(path, "rb")
                        file_handle.seek(start)

                        logger.debug(
                            f"Serving {file_type} report {pk} range {start}-{end}/{file_size}"
                        )

                        response = StreamingHttpResponse(
                            ClosingFileWrapper(file_handle, blksize=8192),
                            status=206,
                            content_type=content_type,
                        )
                        response["Content-Length"] = str(chunk_size)
                        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                        response["Accept-Ranges"] = "bytes"
                        response["Content-Disposition"] = (
                            f'{content_disposition}; filename="{safe_filename}"'
                        )
                        response["Access-Control-Allow-Origin"] = frontend_origin
                        response["Access-Control-Allow-Credentials"] = "true"

                        return response
                    except (OSError, IOError) as e:
                        logger.error(
                            f"Error opening {file_type} report file for range request: {e}"
                        )
                        raise Http404(f"Error accessing {file_type} report file")
                else:
                    logger.warning(f"Invalid Range header format: {range_header}")

            # Serve entire file using FileResponse (automatically handles file closing)
            logger.debug(f"Serving full {file_type} report {pk} ({file_size} bytes)")

            try:
                file_handle = open(path, "rb")
                response = FileResponse(file_handle, content_type=content_type)
                response["Content-Length"] = str(file_size)
                response["Accept-Ranges"] = "bytes"
                response["Content-Disposition"] = (
                    f'{content_disposition}; filename="{safe_filename}"'
                )
                response["Access-Control-Allow-Origin"] = frontend_origin
                response["Access-Control-Allow-Credentials"] = "true"

                # FileResponse will take ownership of file_handle and close it after response
                return response
            except (OSError, IOError) as e:
                logger.error(f"Error opening {file_type} report file: {e}")
                raise Http404(f"Error accessing {file_type} report file")

        except Exception as e:
            logger.error(
                f"Unexpected error streaming {file_type if 'file_type' in locals() else 'report'} {pk}: {e}",
                exc_info=True,
            )
            raise Http404("Error streaming report")
