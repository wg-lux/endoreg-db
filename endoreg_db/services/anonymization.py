# endoreg_db/services/anonymization.py
import logging
from pathlib import Path
from typing import Optional

from django.db import transaction

from endoreg_db.models import RawPdfFile, VideoFile
from endoreg_db.services.pdf_import import PdfImportService
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.utils.storage import ensure_local_file, file_exists

logger = logging.getLogger(__name__)


class AnonymizationService:
    """
    Orchestrates long‑running anonymization tasks so the view only
    does HTTP <-> Service translation.
    """

    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the AnonymizationService with service instances.

        Args:
            project_root: Path to the project root. If None, uses settings.BASE_DIR
        """
        self.project_root: Path = project_root or STORAGE_DIR
        self.video_service = VideoImportService()
        self.pdf_service = PdfImportService()

    # ---------- READ ----------------------------------------------------
    @staticmethod
    def get_status(file_id: int):
        """
        Get anonymization status and media type for a file by its ID.
        
        Parameters:
            file_id (int): Primary key of a VideoFile or RawPdfFile.
        
        Returns:
            dict or None: For a video file, a dict with keys:
                - "mediaType": "video"
                - "anonymizationStatus": the file's anonymization status or "not_started"
                - "fileExists": `True` if the raw video file exists, `False` otherwise
                - "uuid": the file's UUID as a string or `None`
            For a PDF file, a dict with keys:
                - "mediaType": "pdf"
                - "anonymizationStatus": the file's anonymization status or "not_started"
                - "fileExists": `True` if the PDF file exists, `False` otherwise
                - "hash": the PDF file's stored hash
            Returns `None` if no matching file is found.
        """
        vf = VideoFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if vf:
            return {
                "mediaType": "video",
                "anonymizationStatus": vf.state.anonymization_status if vf.state else "not_started",
                "fileExists": file_exists(vf.raw_file),
                "uuid": str(vf.uuid) if vf.uuid else None,
            }

        pdf = RawPdfFile.objects.select_related("state", "sensitive_meta").filter(pk=file_id).first()
        if pdf:
            return {
                "mediaType": "pdf",
                "anonymizationStatus": pdf.state.anonymization_status if pdf.state else "not_started",
                "fileExists": file_exists(pdf.file),
                "hash": pdf.pdf_hash,
            }
        return None

    # ---------- COMMANDS ------------------------------------------------
    @transaction.atomic
    def start(self, file_id: int):
        """
        Start anonymization for a video or PDF file identified by its ID.
        
        If a VideoFile with the given ID exists, marks processing as started (if state present), invokes the video import/anonymization flow, and returns "video". If a RawPdfFile with the given ID exists, validates the PDF file exists in storage, marks processing as started (on state or sensitive_meta), invokes the PDF import/anonymization flow, and returns "pdf". If no matching file is found or the raw source file is missing, returns None. On exception, updates processing-related flags to reflect failure where possible and re-raises the exception.
        
        Returns:
            `\"video\"` if a VideoFile was processed, `\"pdf\"` if a RawPdfFile was processed, `None` if no matching file or required source file is missing.
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
                elif hasattr(vf, "processor") and vf.processor:
                    processor_name = vf.processor.name

                # Get center name
                center_name = vf.center.name if vf.center else "unknown_center"

                # Mark as started
                if vf.state:
                    vf.state.processing_started = True
                    vf.state.save(update_fields=["processing_started"])

                # Use VideoImportService for anonymization
                safe_processor_name = processor_name or "unknown_processor"
                self.video_service.import_and_anonymize(
                    file_path=file_path, center_name=center_name, processor_name=safe_processor_name, save_video=True, delete_source=False
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
                if pdf.state and getattr(pdf.state, "anonymized", False):
                    logger.info(f"RawPdfFile {file_id} already processed, skipping")
                    return "pdf"

                file_field = pdf.file
                if not file_field or not file_field.name:
                    logger.error(f"PDF file not found for RawPdfFile {file_id}")
                    return None

                if not file_exists(file_field):
                    logger.error("PDF file missing from storage for RawPdfFile %s", file_id)
                    return None

                # Get center name
                center_name = pdf.center.name if pdf.center else "unknown_center"

                # Mark as started
                if pdf.state:
                    pdf.state.processing_started = True
                    pdf.state.save(update_fields=["processing_started"])
                elif pdf.sensitive_meta and hasattr(pdf.sensitive_meta, "anonymization_started"):
                    pdf.sensitive_meta.anonymization_started = True
                    pdf.sensitive_meta.save(update_fields=["anonymization_started"])

                with ensure_local_file(file_field) as local_path:
                    self.pdf_service.import_and_anonymize(
                        file_path=local_path,
                        center_name=center_name,
                    )

                logger.info(f"PDF processing completed for RawPdfFile ID: {file_id}")
                return "pdf"

            except Exception as e:
                logger.error(f"Failed to process RawPdfFile {file_id}: {e}")
                # Mark as failed if state exists
                if pdf.state and hasattr(pdf.state, "processing_failed"):
                    pdf.state.processing_failed = True
                    pdf.state.save(update_fields=["processing_failed"])
                elif pdf.sensitive_meta and hasattr(pdf.sensitive_meta, "processing_failed"):
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
            state = vf.state or vf.get_or_create_state()
            if hasattr(state, "mark_anonymization_validated"):
                state.mark_anonymization_validated()
            return "video"

        pdf = RawPdfFile.objects.select_related("state").filter(pk=file_id).first()
        if pdf:
            state = pdf.state or pdf.get_or_create_state()
            if hasattr(state, "mark_anonymization_validated"):
                state.mark_anonymization_validated()
            return "pdf"

        return None

    @staticmethod
    def list_items():
        """
        List all video and PDF files with their anonymization status and timestamps.
        
        Returns:
            A list of dictionaries, one per media file, each containing:
                - id: primary key of the file
                - mediaType: either "video" or "pdf"
                - anonymizationStatus: the file's anonymization status string, or "not_started" if missing
                - createdAt: the file's creation datetime
                - updatedAt: the file's last modification datetime
        """
        video_files = VideoFile.objects.select_related("state").all()
        pdf_files = RawPdfFile.objects.select_related("state").all()  # was sensitive_meta

        data = []
        for vf in video_files:
            data.append(
                {
                    "id": vf.pk,
                    "mediaType": "video",
                    "anonymizationStatus": vf.state.anonymization_status if vf.state else "not_started",
                    "createdAt": vf.date_created,
                    "updatedAt": vf.date_modified,
                }
            )

        for pdf in pdf_files:
            data.append(
                {
                    "id": pdf.pk,
                    "mediaType": "pdf",
                    "anonymizationStatus": pdf.state.anonymization_status if pdf.state else "not_started",
                    "createdAt": pdf.date_created,
                    "updatedAt": pdf.date_modified,
                }
            )
        return data

        return data