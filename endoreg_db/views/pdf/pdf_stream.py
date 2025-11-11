import logging
import os
import re

from django.http import FileResponse, Http404, StreamingHttpResponse
from django.views.decorators.clickjacking import xframe_options_exempt, xframe_options_sameorigin
from rest_framework.views import APIView

from endoreg_db.models import RawPdfFile

from ...utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


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


class PdfStreamView(APIView):
    """
    Streams a PDF file with correct HTTP range support and proper file handle management.

    Supports streaming both raw (original) and processed PDF files.

    Query Parameters:
        type: 'raw' (default) or 'processed' - Selects which PDF file to stream

    Examples:
        GET /api/media/pdf/1/?type=raw - Stream original raw PDF
        GET /api/media/pdf/1/?type=processed - Stream processed PDF
    """

    permission_classes = [EnvironmentAwarePermission]
    @xframe_options_exempt
    def get(self, request, pk: int, *args, **kwargs):
        file_type = "raw"  # Initialize for error logging
        try:
            pdf_obj = RawPdfFile.objects.filter(pk=pk).first()
            if not pdf_obj:
                logger.warning(f"PDF not found: ID {pk}")
                raise Http404("PDF not found")

            # Parse query parameters to determine which file to stream
            file_type = request.query_params.get("type", "raw").lower()
            if file_type not in ["raw", "processed"]:
                logger.warning(f"Invalid file_type '{file_type}', defaulting to 'raw'")
                file_type = "raw"

            # Determine which file field to use
            if file_type == "raw":
                file_field = pdf_obj.file
                if not file_field:
                    logger.warning(f"No raw PDF file available for PDF ID {pk}")
                    raise Http404("Raw PDF file not available")
            else:  # anonymized
                file_field = pdf_obj.anonymized_file
                if not file_field:
                    logger.warning(
                        f"No processed PDF file available for PDF ID {pk}"
                    )
                    raise Http404("Processed PDF file not available")

            # Check if file exists on filesystem
            try:
                file_path = file_field.path
                if not os.path.exists(file_path):
                    logger.error(f"PDF file does not exist on filesystem: {file_path}")
                    raise Http404(
                        f"{file_type.capitalize()} PDF file not found on filesystem"
                    )

                file_size = os.path.getsize(file_path)
            except (OSError, IOError, AttributeError) as e:
                logger.error(f"Error accessing {file_type} PDF file {pk}: {e}")
                raise Http404(f"{file_type.capitalize()} PDF file not accessible")

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

            # Handle Range requests
            range_header = request.headers.get("Range")
            if range_header:
                logger.debug(
                    f"Range request for {file_type} PDF {pk}: {range_header}"
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
                        file_handle = open(file_path, "rb")
                        file_handle.seek(start)

                        logger.debug(
                            f"Serving {file_type} PDF {pk} range {start}-{end}/{file_size}"
                        )

                        response = StreamingHttpResponse(
                            ClosingFileWrapper(file_handle, blksize=8192),
                            status=206,
                            content_type="application/pdf",
                        )
                        response["Content-Length"] = str(chunk_size)
                        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                        response["Accept-Ranges"] = "bytes"
                        response["Content-Disposition"] = (
                            f'inline; filename="{safe_filename}"'
                        )

                        return response
                    except (OSError, IOError) as e:
                        logger.error(
                            f"Error opening {file_type} PDF file for range request: {e}"
                        )
                        raise Http404(f"Error accessing {file_type} PDF file")
                else:
                    logger.warning(f"Invalid Range header format: {range_header}")

            # Serve entire file using FileResponse (automatically handles file closing)
            logger.debug(f"Serving full {file_type} PDF {pk} ({file_size} bytes)")

            try:
                file_handle = open(file_path, "rb")
                response = FileResponse(file_handle, content_type="application/pdf")
                response["Content-Length"] = str(file_size)
                response["Accept-Ranges"] = "bytes"
                response["Content-Disposition"] = f'inline; filename="{safe_filename}"'

                # FileResponse will take ownership of file_handle and close it after response
                return response
            except (OSError, IOError) as e:
                logger.error(f"Error opening {file_type} PDF file: {e}")
                raise Http404(f"Error accessing {file_type} PDF file")

        except Exception as e:
            logger.error(
                f"Unexpected error streaming {file_type if 'file_type' in locals() else 'PDF'} {pk}: {e}",
                exc_info=True,
            )
            raise Http404("Error streaming PDF")
