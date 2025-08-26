from django.http import FileResponse, Http404, StreamingHttpResponse
import mimetypes
import os
import logging
import re
from ...models import RawPdfFile
from ...serializers._old.raw_pdf_meta_validation import PDFFileForMetaSerializer, SensitiveMetaUpdateSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ...models import SensitiveMeta
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.utils.decorators import method_decorator
from django.db import transaction
from django.urls import reverse
from django.utils.encoding import iri_to_uri
from endoreg_db.utils.paths import PDF_DIR, STORAGE_DIR
from .pdf_stream_views import ClosingFileWrapper

logger = logging.getLogger(__name__)
_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")

class PDFMediaView(APIView):
    """
    Unified API for PDFs to support frontend flows:
    - Without `id`: returns next PDF metadata (including anonymized_text) and stream URLs
    - With `id`: streams the PDF (original by default; `?variant=anonymized` for anonymized)
    - Integrates with Media Management expectations (clean deletion after validation is handled elsewhere)
    """

    def get(self, request):
        """
        Handles both:
        - Fetching PDF metadata (if `id` is NOT provided)
        - Streaming the actual PDF file (if `id` is provided)
        """
        pdf_id = request.GET.get("id")
        last_id = request.GET.get("last_id")

        if pdf_id:
            return self.serve_pdf_file(pdf_id)
        else:
            return self.fetch_pdf_metadata(last_id)

    def fetch_pdf_metadata(self, last_id):
        """
        Fetches the first or next available PDF metadata and provides stream URLs.
        """
        pdf_entry = PDFFileForMetaSerializer.get_next_pdf(last_id)
        if pdf_entry is None:
            return Response({"error": "No more PDFs available."}, status=status.HTTP_404_NOT_FOUND)

        serialized_pdf = PDFFileForMetaSerializer(pdf_entry, context={'request': self.request})

        # Build stream URLs pointing to this unified endpoint
        try:
            media_url = reverse('pdf_media')
        except Exception:
            media_url = "/api/pdf/media/"
        stream_url = f"{media_url}?id={pdf_entry.id}"
        anon_stream_url = f"{media_url}?id={pdf_entry.id}&variant=anonymized"

        data = dict(serialized_pdf.data)
        data.update({
            'stream_url': iri_to_uri(self.request.build_absolute_uri(stream_url)),
            'anonymized_stream_url': iri_to_uri(self.request.build_absolute_uri(anon_stream_url)),
            'pdf_id': pdf_entry.id,
            'has_anonymized': bool(getattr(pdf_entry, 'anonymized_file', None) and getattr(pdf_entry.anonymized_file, 'name', None)),
        })
        return Response(data, status=status.HTTP_200_OK)

    @method_decorator(xframe_options_sameorigin)
    def serve_pdf_file(self, pdf_id):
        """
        Streams the actual PDF file (original or anonymized) with Range support.
        Query param `variant=anonymized` selects anonymized file; default is original.
        """
        variant = (self.request.GET.get('variant') or 'original').lower()
        range_header = self.request.headers.get("Range")

        try:
            pdf_entry = RawPdfFile.objects.get(id=pdf_id)
        except RawPdfFile.DoesNotExist:
            return Response({"error": "Invalid PDF ID."}, status=status.HTTP_400_BAD_REQUEST)

        # Choose file according to variant
        file_field = None
        if variant == 'anonymized' and getattr(pdf_entry, 'anonymized_file', None):
            file_field = pdf_entry.anonymized_file
        else:
            file_field = pdf_entry.file

        if not file_field:
            return Response({"error": "PDF file not found."}, status=status.HTTP_404_NOT_FOUND)

        # Resolve path and attempt self-heal for originals
        try:
            file_path = file_field.path
        except Exception:
            file_path = None

        if not file_path or not os.path.exists(file_path):
            if variant != 'anonymized':
                # Try to self-heal original reference to sensitive storage
                sensitive_path = os.path.join(str(PDF_DIR), "sensitive", f"{pdf_entry.pdf_hash}.pdf")
                if os.path.exists(sensitive_path):
                    try:
                        relative_name = os.path.relpath(sensitive_path, str(STORAGE_DIR))
                        if getattr(pdf_entry.file, 'name', None) != relative_name:
                            pdf_entry.file.name = relative_name
                            pdf_entry.save(update_fields=['file'])
                            logger.info("Self-healed PDF file reference for ID %s -> %s", pdf_entry.id, pdf_entry.file.path)
                        file_path = sensitive_path
                    except Exception as e:
                        logger.error("Failed to self-heal file path for PDF %s: %s", pdf_entry.id, e)
                        file_path = sensitive_path
            # If still missing (or anonymized missing), fail
        if not file_path or not os.path.exists(file_path):
            raise Http404("PDF file not found on server.")

        # Prepare headers
        safe_filename = os.path.basename(getattr(file_field, 'name', None) or f"document_{pdf_id}.pdf")
        if not safe_filename.endswith('.pdf'):
            safe_filename += '.pdf'

        file_size = os.path.getsize(file_path)

        # Range support
        if range_header:
            match = _RANGE_RE.match(range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2) or file_size - 1)

                if start < 0 or start >= file_size:
                    raise Http404("Invalid range")
                if end >= file_size:
                    end = file_size - 1

                chunk_size = end - start + 1
                try:
                    fh = open(file_path, 'rb')
                    fh.seek(start)
                    resp = StreamingHttpResponse(
                        ClosingFileWrapper(fh, blksize=8192),
                        status=206,
                        content_type="application/pdf",
                    )
                    resp["Content-Length"] = str(chunk_size)
                    resp["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                    resp["Accept-Ranges"] = "bytes"
                    resp["Content-Disposition"] = f'inline; filename="{safe_filename}"'
                    return resp
                except (OSError, IOError) as e:
                    logger.error(f"Error opening PDF file for range request: {e}")
                    raise Http404("Error accessing PDF file")

        # Fallback: serve full file
        mime_type, _ = mimetypes.guess_type(file_path)
        try:
            fh = open(file_path, 'rb')
            response = FileResponse(fh, content_type=mime_type or "application/pdf")
            response["Content-Length"] = str(file_size)
            response["Accept-Ranges"] = "bytes"
            response["Content-Disposition"] = f'inline; filename="{safe_filename}"'
            return response
        except (OSError, IOError) as e:
            logger.error(f"Error opening PDF file: {e}")
            raise Http404("Error accessing PDF file")

class UpdateSensitiveMetaView(APIView):
    """
    API endpoint to update patient details in the SensitiveMeta table.
    Handles partial updates (only edited fields) and raw file deletion after validation acceptance.
    """

    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        """
        Updates the provided fields for a specific patient record.
        Only updates fields that are sent in the request.
        Automatically deletes raw PDF files when validation is accepted.
        """
        sensitive_meta_id = request.data.get("sensitive_meta_id")

        if not sensitive_meta_id:
            return Response({"error": "sensitive_meta_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sensitive_meta = SensitiveMeta.objects.get(id=sensitive_meta_id)
        except SensitiveMeta.DoesNotExist:
            return Response({"error": "Patient record not found."}, status=status.HTTP_404_NOT_FOUND)

        is_accepting_validation = request.data.get("is_verified", False)
        delete_raw_files = request.data.get("delete_raw_files", False)
        if is_accepting_validation:
            delete_raw_files = True
            logger.info(f"Validation accepted for PDF SensitiveMeta {sensitive_meta_id}, marking raw files for deletion")

        serializer = SensitiveMetaUpdateSerializer(sensitive_meta, data=request.data, partial=True)

        if serializer.is_valid():
            updated_sm = serializer.save()
            if delete_raw_files and updated_sm.is_verified:
                try:
                    pdf_file = RawPdfFile.objects.filter(sensitive_meta=updated_sm).first()
                    if pdf_file:
                        self._schedule_raw_file_deletion(pdf_file)
                        logger.info(f"Scheduled raw file deletion for PDF {pdf_file.id}")
                    else:
                        logger.warning(f"No PDF file found for SensitiveMeta {sensitive_meta_id}")
                except Exception as e:
                    logger.error(f"Error scheduling raw file deletion for PDF SensitiveMeta {sensitive_meta_id}: {e}")
            return Response({"message": "Patient information updated successfully.", "updated_data": serializer.data}, status=status.HTTP_200_OK)

        return Response({"error": "Invalid data.", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    def _schedule_raw_file_deletion(self, pdf_file):
        """
        Schedule deletion of raw PDF file after validation acceptance.
        Deletes the original (sensitive) file but keeps anonymized_file for frontend.
        """
        try:
            def cleanup_raw_files():
                try:
                    if pdf_file.file and getattr(pdf_file.file, 'path', None) and os.path.exists(pdf_file.file.path):
                        logger.info(f"Deleting original (sensitive) PDF file: {pdf_file.file.path}")
                        os.remove(pdf_file.file.path)
                        pdf_file.file = None
                        pdf_file.save(update_fields=['file'])
                        logger.info(f"Successfully deleted original file for PDF {pdf_file.id}")
                    else:
                        logger.info(f"Original file already deleted or not found for PDF {pdf_file.id}")
                except Exception as e:
                    logger.error(f"Error during raw file cleanup for PDF {pdf_file.id}: {e}")
            transaction.on_commit(cleanup_raw_files)
        except Exception as e:
            logger.error(f"Error scheduling raw file deletion for PDF {pdf_file.id}: {e}")
            raise