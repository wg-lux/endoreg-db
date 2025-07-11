"""
PDF streaming views with HTTP range support.
Provides immediate PDF rendering in browsers through byte-range streaming.
"""
import re
import logging
from wsgiref.util import FileWrapper
from django.http import (
    FileResponse,
    StreamingHttpResponse,
    Http404
)
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from endoreg_db.models import RawPdfFile
from ..utils.permissions import EnvironmentAwarePermission

logger = logging.getLogger(__name__)

# Regex for parsing Range header: "bytes=start-end"
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


class PDFStreamView(APIView):
    """
    Streams a PDF file with correct HTTP range support so browsers
    can start displaying immediately and seek within the document.
    
    Features:
    - Supports HTTP Range requests (206 Partial Content)
    - Uses streaming response for memory efficiency
    - Sets correct headers for inline PDF display
    - Compatible with all major browsers
    """
    permission_classes = [EnvironmentAwarePermissions]

    def get(self, request, pdf_id: int, *args, **kwargs):
        """
        Stream PDF file with range support.
        
        Args:
            pdf_id: Primary key of the RawPdfFile to stream
            
        Returns:
            StreamingHttpResponse (206) for range requests
            FileResponse (200) for full file requests
            
        Raises:
            Http404: If PDF not found or file not accessible
        """
        try:
            pdf_obj = RawPdfFile.objects.filter(pk=pdf_id).first()
            if not pdf_obj or not pdf_obj.file:
                logger.warning(f"PDF not found: ID {pdf_id}")
                raise Http404("PDF not found")

            # Get file size and open handle
            try:
                file_size = pdf_obj.file.size
                file_handle = pdf_obj.file.open("rb")
                safe_filename = pdf_obj.file.name.split('/')[-1] if pdf_obj.file.name else f"document_{pdf_id}.pdf"

            except (OSError, IOError) as e:
                logger.error(f"Error accessing PDF file {pdf_id}: {e}")
                raise Http404("PDF file not accessible")

                # Generate safe filename
            if not safe_filename.endswith('.pdf'):
                safe_filename += '.pdf'

            # --- Handle Range header (streaming for partial content) ---
            range_header = request.headers.get("Range")
            if range_header:
                logger.debug(f"Range request for PDF {pdf_id}: {range_header}")
                match = _RANGE_RE.match(range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2) or file_size - 1)
                    
                    # Validate range
                    if start >= file_size:
                        file_handle.close()
                        logger.warning(f"Invalid range start {start} for file size {file_size}")
                        raise Http404("Invalid range")
                        
                    if end >= file_size:
                        end = file_size - 1
                    
                    chunk_size = end - start + 1
                    file_handle.seek(start)

                    logger.debug(f"Serving PDF {pdf_id} range {start}-{end}/{file_size}")
                    
                    response = StreamingHttpResponse(
                        FileWrapper(file_handle, blksize=8192),
                        status=206,
                        content_type="application/pdf",
                    )
                    response["Content-Length"] = str(chunk_size)
                    response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                    response["Accept-Ranges"] = "bytes"
                    response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
                    
                    return response
                else:
                    logger.warning(f"Invalid Range header format: {range_header}")

            # --- Fallback: serve entire file ---
            logger.debug(f"Serving full PDF {pdf_id} ({file_size} bytes)")
            
            response = FileResponse(
                file_handle, 
                content_type="application/pdf"
            )
            response["Content-Length"] = str(file_size)
            response["Accept-Ranges"] = "bytes"
            response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
            
            return response

        except Exception as e:
            logger.error(f"Unexpected error streaming PDF {pdf_id}: {e}", exc_info=True)
            raise Http404("Error streaming PDF")
        
        finally:
            if 'file_handle' in locals() and not file_handle.closed:
                file_handle.close()