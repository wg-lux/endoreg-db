import re
import logging
from django.http import FileResponse, StreamingHttpResponse, Http404
from rest_framework.views import APIView
from ...utils.permissions import EnvironmentAwarePermission
from endoreg_db.models import RawPdfFile
import os
from django.views.decorators.clickjacking import xframe_options_sameorigin

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
        if hasattr(self.file_handle, 'close'):
            self.file_handle.close()
            
class PDFStreamView(APIView):
    """
    Streams a PDF file with correct HTTP range support and proper file handle management.
    """
    permission_classes = [EnvironmentAwarePermission]

    @xframe_options_sameorigin
    def get(self, request, pdf_id: int, *args, **kwargs):
        try:
            pdf_obj = RawPdfFile.objects.filter(pk=pdf_id).first()
            if not pdf_obj or not pdf_obj.file:
                logger.warning(f"PDF not found: ID {pdf_id}")
                raise Http404("PDF not found")

            # Check if file exists on filesystem
            try:
                file_path = pdf_obj.file.path
                if not os.path.exists(file_path):
                    logger.error(f"PDF file does not exist on filesystem: {file_path}")
                    raise Http404("PDF file not found on filesystem")
                    
                file_size = os.path.getsize(file_path)
            except (OSError, IOError, AttributeError) as e:
                logger.error(f"Error accessing PDF file {pdf_id}: {e}")
                raise Http404("PDF file not accessible")

            # Generate safe filename
            safe_filename = os.path.basename(pdf_obj.file.name) if pdf_obj.file.name else f"document_{pdf_id}.pdf"
            if not safe_filename.endswith('.pdf'):
                safe_filename += '.pdf'

            # Handle Range requests
            range_header = request.headers.get("Range")
            if range_header:
                logger.debug(f"Range request for PDF {pdf_id}: {range_header}")
                match = _RANGE_RE.match(range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2) or file_size - 1)
                    
                    # Validate range
                    if start >= file_size or start < 0:
                        logger.warning(f"Invalid range start {start} for file size {file_size}")
                        raise Http404("Invalid range")
                        
                    if end >= file_size:
                        end = file_size - 1
                    
                    chunk_size = end - start + 1
                    
                    try:
                        file_handle = open(file_path, "rb")
                        file_handle.seek(start)
                        
                        logger.debug(f"Serving PDF {pdf_id} range {start}-{end}/{file_size}")
                        
                        response = StreamingHttpResponse(
                            ClosingFileWrapper(file_handle, blksize=8192),
                            status=206,
                            content_type="application/pdf",
                        )
                        response["Content-Length"] = str(chunk_size)
                        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                        response["Accept-Ranges"] = "bytes"
                        response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
                        
                        return response
                    except (OSError, IOError) as e:
                        logger.error(f"Error opening PDF file for range request: {e}")
                        raise Http404("Error accessing PDF file")
                else:
                    logger.warning(f"Invalid Range header format: {range_header}")

            # Serve entire file using FileResponse (automatically handles file closing)
            logger.debug(f"Serving full PDF {pdf_id} ({file_size} bytes)")
            
            try:
                file_handle = open(file_path, "rb")
                response = FileResponse(
                    file_handle, 
                    content_type="application/pdf"
                )
                response["Content-Length"] = str(file_size)
                response["Accept-Ranges"] = "bytes"
                response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
                
                # FileResponse will take ownership of file_handle and close it after response
                return response
            except (OSError, IOError) as e:
                logger.error(f"Error opening PDF file: {e}")
                raise Http404("Error accessing PDF file")

        except Exception as e:
            logger.error(f"Unexpected error streaming PDF {pdf_id}: {e}", exc_info=True)
            raise Http404("Error streaming PDF")