import logging

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import RawPdfFile, SensitiveMeta
from ...services.pdf_import import PdfImportService

logger = logging.getLogger(__name__)


class PdfReimportView(APIView):
    """
    API endpoint to re-import a pdf file and regenerate metadata.
    This is useful when OCR failed or metadata is incomplete.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pdf_service = PdfImportService()

    def post(self, request, pk):
        """
        Re-import a pdf file to regenerate SensitiveMeta and other metadata.
        Instead of creating a new pdf, this updates the existing one.

        Args:
            request: HTTP request object
            pk: PDF primary key (ID)
        """
        pdf_id = pk  # Align with media framework naming convention

        # Validate pdf_id parameter
        if not pdf_id or not isinstance(pdf_id, int):
            return Response(
                {"error": "Invalid PDF ID provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pdf = RawPdfFile.objects.get(id=pdf_id)
            logger.info(f"Found PDF {pdf.pdf_hash} (ID: {pdf_id}) for re-import")
        except RawPdfFile.DoesNotExist:
            logger.warning(f"PDF with ID {pdf_id} not found")
            return Response(
                {"error": f"PDF with ID {pdf_id} not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get raw file path using the model method
        raw_file_path = pdf.get_raw_file_path()

        if not raw_file_path or not raw_file_path.exists():
            logger.error(f"Raw PDF file not found for hash {pdf.pdf_hash}: {raw_file_path}")
            return Response(
                {"error": f"Raw PDF file not found for PDF {pdf.pdf_hash}. Please upload the original file again."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check if PDF has required relationships
        if not pdf.center:
            logger.warning(f"PDF {pdf.pdf_hash} has no associated center")
            return Response(
                {"error": "PDF has no associated center."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            logger.info(f"Starting re-import for PDF {pdf.pdf_hash} (ID: {pdf_id})")

            with transaction.atomic():
                # Clear existing metadata to force regeneration
                old_meta_id = None
                if pdf.sensitive_meta:
                    old_meta_id = pdf.sensitive_meta.pk
                    logger.info(f"Clearing existing SensitiveMeta {old_meta_id} for PDF {pdf.pdf_hash}")
                    pdf.sensitive_meta = None
                    pdf.save(update_fields=["sensitive_meta"])

                    # Delete the old SensitiveMeta record
                    try:
                        SensitiveMeta.objects.filter(pk=old_meta_id).delete()
                        logger.info(f"Deleted old SensitiveMeta {old_meta_id}")
                    except Exception as e:
                        logger.warning(f"Could not delete old SensitiveMeta {old_meta_id}: {e}")

                # Use PdfImportService for reprocessing
                try:
                    logger.info(f"Starting reprocessing using PdfImportService for {pdf.pdf_hash}")
                    self.pdf_service.import_and_anonymize(
                        file_path=raw_file_path,
                        center_name=pdf.center.name,
                        delete_source=False,  # Don't delete during reimport
                        retry=True,  # Mark as retry attempt
                    )

                    logger.info(f"PdfImportService reprocessing completed for {pdf.pdf_hash}")

                    # Refresh to get updated state
                    pdf.refresh_from_db()

                    return Response(
                        {
                            "message": "PDF re-import completed successfully.",
                            "pdf_id": pdf_id,
                            "pdf_hash": str(pdf.pdf_hash),
                            "sensitive_meta_created": pdf.sensitive_meta is not None,
                            "sensitive_meta_id": pdf.sensitive_meta.pk if pdf.sensitive_meta else None,
                            "text_extracted": bool(pdf.text),
                            "anonymized": pdf.anonymized,
                            "status": "done",
                        },
                        status=status.HTTP_200_OK,
                    )

                except Exception as e:
                    logger.exception(f"PdfImportService reprocessing failed for PDF {pdf.pdf_hash}: {e}")
                    return Response(
                        {
                            "error": f"Reprocessing failed: {str(e)}",
                            "error_type": "processing_error",
                            "pdf_id": pdf_id,
                            "pdf_hash": str(pdf.pdf_hash),
                        },
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

        except Exception as e:
            logger.error(f"Failed to re-import PDF {pdf.pdf_hash}: {str(e)}", exc_info=True)

            # Handle specific error types
            error_msg = str(e)
            if any(phrase in error_msg.lower() for phrase in ["insufficient storage", "no space left", "disk full"]):
                # Storage error - return specific error message
                return Response(
                    {
                        "error": f"Storage error during re-import: {error_msg}",
                        "error_type": "storage_error",
                        "pdf_id": pdf_id,
                        "pdf_hash": str(pdf.pdf_hash),
                    },
                    status=status.HTTP_507_INSUFFICIENT_STORAGE,
                )
            else:
                # Other errors
                return Response(
                    {
                        "error": f"Re-import failed: {error_msg}",
                        "error_type": "processing_error",
                        "pdf_id": pdf_id,
                        "pdf_hash": str(pdf.pdf_hash),
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
