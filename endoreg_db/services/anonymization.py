# endoreg_db/services/anonymization.py
from pathlib import Path
from django.db import transaction
from django.conf import settings
from endoreg_db.models import VideoFile, RawPdfFile
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.pdf_import import PdfImportService
from endoreg_db.utils.paths import STORAGE_DIR
import logging

logger = logging.getLogger(__name__)

class AnonymizationService:
    """
    Orchestrates long‑running anonymization tasks so the view only
    does HTTP <-> Service translation.
    """
    
    def __init__(self, project_root: Path = None):
        """
        Initialize the AnonymizationService with service instances.
        
        Args:
            project_root: Path to the project root. If None, uses settings.BASE_DIR
        """
        if project_root is None:
            project_root = STORAGE_DIR
        
        self.video_service = VideoImportService()
        self.pdf_service = PdfImportService()

    # ---------- READ ----------------------------------------------------
    @staticmethod
    def get_status(file_id: int):
        """
        Retrieve the anonymization status and media type for a file by its ID.
        
        Returns:
            dict or None: A dictionary containing the file's media type and anonymization status if found, or None if no matching file exists.
        """
        vf = VideoFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if vf:
            return {
                "mediaType": "video",
                "anonymizationStatus": vf.state.anonymization_status if vf.state else "not_started",
                "fileExists": bool(vf.raw_file and hasattr(vf.raw_file, 'path')),
                "uuid": str(vf.uuid) if vf.uuid else None,
            }

        pdf = RawPdfFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if pdf:
            return {
                "mediaType": "pdf",
                "anonymizationStatus": pdf.state.anonymization_status if pdf.state else "not_started",
                "fileExists": bool(pdf.file and hasattr(pdf.file, 'path')),
                "hash": pdf.pdf_hash,
            }
        return None

    # ---------- COMMANDS ------------------------------------------------
    @transaction.atomic
    def start(self, file_id: int):
        """
        Start anonymization process for a file by its ID.
        
        Args:
            file_id: The ID of the file to anonymize
            
        Returns:
            str or None: Media type if successful, None if file not found
        """
        # Try VideoFile first
        vf = VideoFile.objects.select_related("state", "sensitive_meta", "center", "video_meta__processor").filter(pk=file_id).first()
        if vf:
            try:
                logger.info(f"Starting video anonymization for VideoFile ID: {file_id}")
                
                # Check if already processed
                if vf.state and vf.state.anonymized:
                    logger.info(f"VideoFile {file_id} already anonymized, skipping")
                    return "video"
                
                # Get file path
                file_path = vf.get_raw_file_path()
                if not file_path or not Path(file_path).exists():
                    logger.error(f"Raw file not found for VideoFile {file_id}: {file_path}")
                    return None
                
                # Get processor name
                processor_name = None
                if vf.video_meta and vf.video_meta.processor:
                    processor_name = vf.video_meta.processor.name
                elif hasattr(vf, 'processor') and vf.processor:
                    processor_name = vf.processor.name
                
                # Get center name
                center_name = vf.center.name if vf.center else "unknown_center"
                
                # Mark as started
                if vf.state:
                    vf.state.processing_started = True
                    vf.state.save(update_fields=["processing_started"])
                
                # Use VideoImportService for anonymization
                self.video_service.import_and_anonymize(
                    file_path=file_path,
                    center_name=center_name,
                    processor_name=processor_name,
                    save_video=True,
                    delete_source=False
                )
                
                logger.info(f"Video anonymization completed for VideoFile ID: {file_id}")
                return "video"
                
            except Exception as e:
                logger.error(f"Failed to anonymize VideoFile {file_id}: {e}")
                # Mark as failed if state exists
                if vf.state:
                    vf.state.processing_started = False  # Mark processing as not started due to failure
                    vf.state.save(update_fields=["processing_started"])
                raise

        # Try RawPdfFile
        pdf = RawPdfFile.objects.select_related("state", "sensitive_meta", "center").filter(pk=file_id).first()
        if pdf:
            try:
                logger.info(f"Starting PDF processing for RawPdfFile ID: {file_id}")
                
                # Check if already processed
                if pdf.state and getattr(pdf.state, 'anonymized', False):
                    logger.info(f"RawPdfFile {file_id} already processed, skipping")
                    return "pdf"
                
                # Get file path
                if not pdf.file or not hasattr(pdf.file, 'path'):
                    logger.error(f"PDF file not found for RawPdfFile {file_id}")
                    return None
                
                file_path = Path(pdf.file.path)
                if not file_path.exists():
                    logger.error(f"PDF file does not exist: {file_path}")
                    return None
                
                # Get center name
                center_name = pdf.center.name if pdf.center else "unknown_center"
                
                # Mark as started
                if pdf.state:
                    pdf.state.processing_started = True
                    pdf.state.save(update_fields=["processing_started"])
                elif pdf.sensitive_meta:
                    pdf.sensitive_meta.anonymization_started = True
                    pdf.sensitive_meta.save(update_fields=["anonymization_started"])
                
                
                # Use PdfImportService for processing
                # Use PdfImportService for processing
                self.pdf_service.import_and_anonymize(
                    file_path=file_path,
                    center_name=center_name,
                )
                
                logger.info(f"PDF processing completed for RawPdfFile ID: {file_id}")
                return "pdf"
                
            except Exception as e:
                logger.error(f"Failed to process RawPdfFile {file_id}: {e}")
                # Mark as failed if state exists
                if pdf.state:
                    pdf.state.processing_failed = True
                    pdf.state.save(update_fields=["processing_failed"])
                elif pdf.sensitive_meta:
                    pdf.sensitive_meta.processing_failed = True
                    pdf.sensitive_meta.save(update_fields=["processing_failed"])
                raise

        logger.warning(f"No file found with ID: {file_id}")
        return None

    @staticmethod
    @transaction.atomic
    def validate(file_id: int):
        vf = VideoFile.objects.select_related("state").filter(pk=file_id).first()
        if vf:
            vf.state.mark_anonymization_validated()
            return "video"

        pdf = RawPdfFile.objects.select_related("state").filter(pk=file_id).first()
        if pdf:
            pdf.state.mark_anonymization_validated()
            return "pdf"

        return None
    
    def list_items():
        video_files = VideoFile.objects.select_related("state").all()
        pdf_files = RawPdfFile.objects.select_related("state").all()  # was sensitive_meta

        data = []
        for vf in video_files:
            data.append({
                "id": vf.id,
                "mediaType": "video",
                "anonymizationStatus": vf.state.anonymization_status if vf.state else "not_started",
                "createdAt": vf.date_created,
                "updatedAt": vf.date_modified,
            })
            
            

        for pdf in pdf_files:
            data.append({
                "id": pdf.id,
                "mediaType": "pdf",
                "anonymizationStatus": pdf.state.anonymization_status if pdf.state else "not_started",
                "createdAt": pdf.date_created,
                "updatedAt": pdf.date_modified,
            })
        return data


        return data
