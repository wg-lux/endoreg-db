from django.http import FileResponse, Http404
import mimetypes
import os
import logging
from ...models import RawPdfFile
from ...serializers._old.raw_pdf_meta_validation import PDFFileForMetaSerializer, SensitiveMetaUpdateSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from ...models import SensitiveMeta
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.utils.decorators import method_decorator
from django.db import transaction

logger = logging.getLogger(__name__)

class PDFFileForMetaView(APIView):
    """
    API endpoint to:
    - Fetch PDF metadata if `id` is NOT provided.
    - Serve the actual PDF file if `id` is provided.
    """

    def get(self, request):
        """
        Handles both:
        Fetching PDF metadata** (if `id` is NOT provided)
         Serving the actual PDF file** (if `id` is provided)
        """

        pdf_id = request.GET.get("id")  # Check if 'id' is provided in the query params
        last_id = request.GET.get("last_id")  # Check if 'last_id' is provided for pagination

        if pdf_id:
            return self.serve_pdf_file(pdf_id)  # Serve the actual PDF file
        else:
            return self.fetch_pdf_metadata(last_id)  # Fetch metadata for the first or next PDF

    def fetch_pdf_metadata(self, last_id):
        """
        Fetches the first or next available PDF metadata.
        """
        pdf_entry = PDFFileForMetaSerializer.get_next_pdf(last_id)

        if pdf_entry is None:
            return Response({"error": "No more PDFs available."}, status=status.HTTP_404_NOT_FOUND)

        serialized_pdf = PDFFileForMetaSerializer(pdf_entry, context={'request': self.request})

        print("Debugging API Response:")
        print("Serialized Data:", serialized_pdf.data)  # Debugging
        return Response(serialized_pdf.data, status=status.HTTP_200_OK)
    
    @method_decorator(xframe_options_sameorigin)
    def serve_pdf_file(self, pdf_id):
        """
        Serves the actual PDF file for download or viewing.
        Allows iframe embedding from same origin.
        """
        try:
            pdf_entry = RawPdfFile.objects.get(id=pdf_id)  # Get the PDF file by ID

            if not pdf_entry.file:
                return Response({"error": "PDF file not found."}, status=status.HTTP_404_NOT_FOUND)

            full_pdf_path = pdf_entry.file.path  # Get the absolute file path

            if not os.path.exists(full_pdf_path):
                raise Http404("PDF file not found on server.")

            mime_type, _ = mimetypes.guess_type(full_pdf_path)  # Detect file type
            response = FileResponse(open(full_pdf_path, "rb"), content_type=mime_type or "application/pdf")

            # Enhanced headers for iframe compatibility
            response["Content-Disposition"] = f'inline; filename="{os.path.basename(full_pdf_path)}"'  # Allows direct viewing
            response["X-Frame-Options"] = "SAMEORIGIN"  # Explicitly allow same-origin iframe embedding
            response["Cache-Control"] = "public, max-age=3600"  # Cache for 1 hour
            
            return response  # Sends the PDF file as a stream

        except RawPdfFile.DoesNotExist:
            return Response({"error": "Invalid PDF ID."}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return Response({"error": f"Internal error: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
        sensitive_meta_id = request.data.get("sensitive_meta_id")  # Required field

        if not sensitive_meta_id:
            return Response({"error": "sensitive_meta_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sensitive_meta = SensitiveMeta.objects.get(id=sensitive_meta_id)
        except SensitiveMeta.DoesNotExist:
            return Response({"error": "Patient record not found."}, status=status.HTTP_404_NOT_FOUND)

        # Check if this is a validation acceptance (is_verified being set to True)
        is_accepting_validation = request.data.get("is_verified", False)
        delete_raw_files = request.data.get("delete_raw_files", False)
        
        # If user is accepting validation, automatically set delete_raw_files to True
        if is_accepting_validation:
            delete_raw_files = True
            logger.info(f"Validation accepted for PDF SensitiveMeta {sensitive_meta_id}, marking raw files for deletion")

        # Serialize the request data with partial=True to allow partial updates
        serializer = SensitiveMetaUpdateSerializer(sensitive_meta, data=request.data, partial=True)

        if serializer.is_valid():
            updated_sm = serializer.save()
            
            # Handle raw file deletion if requested or if validation was accepted
            if delete_raw_files and updated_sm.is_verified:
                try:
                    # Find associated PDF file
                    pdf_file = RawPdfFile.objects.filter(sensitive_meta=updated_sm).first()
                    if pdf_file:
                        self._schedule_raw_file_deletion(pdf_file)
                        logger.info(f"Scheduled raw file deletion for PDF {pdf_file.id}")
                    else:
                        logger.warning(f"No PDF file found for SensitiveMeta {sensitive_meta_id}")
                except Exception as e:
                    logger.error(f"Error scheduling raw file deletion for PDF SensitiveMeta {sensitive_meta_id}: {e}")
                    # Don't fail the entire request if deletion scheduling fails
            
            return Response({"message": "Patient information updated successfully.", "updated_data": serializer.data},
                            status=status.HTTP_200_OK)
        
        return Response({"error": "Invalid data.", "details": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    
    def _schedule_raw_file_deletion(self, pdf_file):
        """
        Schedule deletion of raw PDF file after validation acceptance.
        """
        try:
            def cleanup_raw_files():
                """Cleanup function to be called after transaction commit"""
                try:
                    if pdf_file.file and os.path.exists(pdf_file.file.path):
                        logger.info(f"Deleting raw PDF file: {pdf_file.file.path}")
                        os.remove(pdf_file.file.path)
                        pdf_file.file = None
                        pdf_file.save()
                        logger.info(f"Successfully deleted raw PDF file {pdf_file.id}")
                    else:
                        logger.info(f"Raw PDF file already deleted or not found for PDF {pdf_file.id}")
                        
                except Exception as e:
                    logger.error(f"Error during raw file cleanup for PDF {pdf_file.id}: {e}")
            
            # Schedule cleanup after transaction commits
            transaction.on_commit(cleanup_raw_files)
            
        except Exception as e:
            logger.error(f"Error scheduling raw file deletion for PDF {pdf_file.id}: {e}")
            raise
