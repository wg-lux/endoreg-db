from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
import logging
from pathlib import Path
from django.db import transaction
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
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            pdf = RawPdfFile.objects.get(id=pdf_id)
            logger.info(f"Found PDF {pdf.uuid} (ID: {pdf_id}) for re-import")
        except RawPdfFile.DoesNotExist:
            logger.warning(f"PDF with ID {pdf_id} not found")
            return Response(
                {"error": f"PDF with ID {pdf_id} not found."}, 
                status=status.HTTP_404_NOT_FOUND
            )



        # Check if the raw file actually exists on disk
        raw_file_path = Path(pdf.file.path)
        if not raw_file_path.exists():
            logger.error(f"Raw file not found on disk: {raw_file_path}")
            return Response(
                {"error": f"PDF file not found on server: {raw_file_path.name}"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if PDF has required relationships
        if not pdf.center:
            logger.warning(f"PDF {pdf.uuid} has no associated center")
            return Response(
                {"error": "Video has no associated center."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            logger.info(f"Starting in-place re-import for pdf {pdf.uuid} (ID: {pdf_id})")
            
            with transaction.atomic():
                # Clear existing metadata to force regeneration
                old_meta_id = None
                if pdf.sensitive_meta:
                    old_meta_id = pdf.sensitive_meta.id
                    logger.info(f"Clearing existing SensitiveMeta {old_meta_id} for pdf {pdf.uuid}")
                    pdf.sensitive_meta = None
                    pdf.save(update_fields=['sensitive_meta'])
                    
                    # Delete the old SensitiveMeta record
                    try:
                        SensitiveMeta.objects.filter(id=old_meta_id).delete()
                        logger.info(f"Deleted old SensitiveMeta {old_meta_id}")
                    except Exception as e:
                        logger.warning(f"Could not delete old SensitiveMeta {old_meta_id}: {e}")
      
                
        
                
                
                # Ensure minimum patient data is available
                logger.info(f"Ensuring minimum patient data for {pdf.uuid}")
                self.pdf_service._ensure_default_patient_data(pdf)
                
                # Refresh from database to get updated data
                pdf.refresh_from_db()
                
                # Use VideoImportService for anonymization
                try:
                    
                    logger.info(f"Starting anonymization using VideoImportService for {pdf.uuid}")
                    self.pdf_service.import_and_anonymize(
                        file_path=raw_file_path,
                        center_name=pdf.center.name,
                        processor_name=pdf.processor.name if pdf.processor else "Unknown",
                        save_video=True,
                        delete_source=False
                    )
                    
                    logger.info(f"VideoImportService anonymization completed for {pdf.uuid}")
                    
                    
                    return Response({
                        "message": "Video re-import with VideoImportService completed successfully.",
                        "pdf_id": pdf_id,
                        "uuid": str(pdf.uuid),
                        "frame_cleaning_applied": True,
                        "sensitive_meta_created": pdf.sensitive_meta is not None,
                        "sensitive_meta_id": pdf.sensitive_meta.id if pdf.sensitive_meta else None,
                        "updated_in_place": True,
                        "status": "done"
                    }, status=status.HTTP_200_OK)
                    
                except Exception as e:
                    logger.exception(f"VideoImportService anonymization failed for pdf {pdf.uuid}: {e}")
                    logger.warning("Continuing without anonymization due to error")
                
            # Refresh from database to get final state
            pdf.refresh_from_db()
            
            return Response({
                "message": "PDF re-import completed successfully.",
                "pdf_id": pdf_id,
                "uuid": str(pdf.uuid),
                "sensitive_meta_created": pdf.sensitive_meta is not None,
                "sensitive_meta_id": pdf.sensitive_meta.id if pdf.sensitive_meta else None,
                "updated_in_place": True,
                "status": "done"
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Failed to re-import pdf {pdf.uuid}: {str(e)}", exc_info=True)
            
            # Handle specific error types
            error_msg = str(e)
            if any(phrase in error_msg.lower() for phrase in ["insufficient storage", "no space left", "disk full"]):
                # Storage error - return specific error message
                return Response({
                    "error": f"Storage error during re-import: {error_msg}",
                    "error_type": "storage_error",
                    "pdf_id": pdf_id,
                    "uuid": str(pdf.uuid)
                }, status=status.HTTP_507_INSUFFICIENT_STORAGE)
            else:
                # Other errors
                return Response({
                    "error": f"Re-import failed: {error_msg}",
                    "error_type": "processing_error", 
                    "pdf_id": pdf_id,
                    "uuid": str(pdf.uuid)
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
