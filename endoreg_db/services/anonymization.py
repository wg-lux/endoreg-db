# endoreg_db/services/anonymization.py
import logging
from pathlib import Path
from typing import Optional, Literal

from django.db import transaction

from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.lx_video_contracts import resolve_lx_anonymization_state
from endoreg_db.services.video_import import VideoImportService
from endoreg_db.services.video_files import get_or_create_video_state
from endoreg_db.services.raw_pdf_files import get_or_create_raw_pdf_state
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.utils.filesystem.paths import STORAGE_DIR
from endoreg_db.utils.storage import ensure_local_file, file_exists

logger = logging.getLogger(__name__)


def _video_integrity_status(video: VideoFile) -> tuple[str, str]:
    payload = video.meta if isinstance(video.meta, dict) else {}
    integrity_status = str(payload.get("integrity_status") or "").strip()
    integrity_error = str(payload.get("integrity_error") or "").strip()
    if not integrity_status and bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    ):
        integrity_status = "lost"
    return integrity_status, integrity_error


def _video_has_integrity_failure(video: VideoFile) -> bool:
    integrity_status, _ = _video_integrity_status(video)
    return integrity_status == "lost" or bool(
        getattr(getattr(video, "state", None), "processing_error", False)
    )


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
        self.pdf_service = ReportImportService()

    @staticmethod
    def get_status(file_id: int, kind: Optional[str] = None) -> Optional[dict]:
        """
        Retrieve status.
        Handles 'pdf' vs 'report' alias.
        If kind is None, checks both tables (Video priority).
        """

        # 1. Normalize the input kind if legacy name pdf is used
        if kind == "pdf":
            kind = "report"

        # 2. Define lookup logic
        check_video = kind == "video" or kind is None
        check_report = kind == "report" or kind is None

        # 3. Check VideoFile
        if check_video:
            vf = (
                VideoFile.objects.select_related("state", "sensitive_meta")
                .filter(pk=file_id)
                .first()
            )
            if vf:
                integrity_status, integrity_error = _video_integrity_status(vf)
                return {
                    "media_type": "video",
                    "anonymization_status": resolve_lx_anonymization_state(vf).value,
                    "integrity_status": integrity_status,
                    "integrity_error": integrity_error,
                    "file_exists": file_exists(vf.raw_file),
                    "uuid": str(vf.video_hash) if vf.video_hash else None,
                }

        # 4. Check RawPdfFile
        if check_report:
            pdf = (
                RawPdfFile.objects.select_related("state", "sensitive_meta")
                .filter(pk=file_id)
                .first()
            )
            if pdf:
                anonymization_status = (
                    pdf.state.anonymization_status if pdf.state else "not_started"
                )
                return {
                    "media_type": "pdf",
                    "anonymization_status": anonymization_status,
                    "file_exists": file_exists(pdf.file),
                    "hash": pdf.pdf_hash,
                }

        # 5. Not found in either (or the specific requested type wasn't found)
        return None

    # ---------- COMMANDS ------------------------------------------------
    @transaction.atomic
    def start(self, file_id: int, kind: Optional[str] = None) -> Optional[str]:
        """
        Start anonymization process for a file by its ID.

        Args:
            file_id: The ID of the file to anonymize

        Returns:
            str or None: Media type if successful, None if file not found
        """
        # Try VideoFile first
        if kind == "video" or kind is None:
            vf = (
                VideoFile.objects.select_related(
                    "state", "sensitive_meta", "center", "video_meta__processor"
                )
                .filter(pk=file_id)
                .first()
            )
            if vf:
                try:
                    logger.info(
                        f"Starting video anonymization for VideoFile ID: {file_id}"
                    )

                    if _video_has_integrity_failure(vf):
                        integrity_status, integrity_error = _video_integrity_status(vf)
                        logger.error(
                            "Refusing anonymization for failed/lost VideoFile %s "
                            "(hash=%s, integrity_status=%s, reason=%s)",
                            file_id,
                            vf.video_hash,
                            integrity_status,
                            integrity_error,
                        )
                        return None

                    # Check if already processed
                    if vf.state and vf.state.anonymized:
                        logger.info(f"VideoFile {file_id} already anonymized, skipping")
                        return "video"

                    raw_file = vf.raw_file
                    if not raw_file or not raw_file.name or not file_exists(raw_file):
                        logger.error(
                            "Raw file not found for VideoFile %s in storage",
                            file_id,
                        )
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
                        vf.state.mark_processing_started()

                    # Use VideoImportService for anonymization
                    safe_processor_name = processor_name or "unknown_processor"
                    with ensure_local_file(raw_file) as file_path:
                        self.video_service.import_and_anonymize(
                            file_path=file_path,
                            center_name=center_name,
                            processor_name=safe_processor_name,
                        )

                    logger.info(
                        f"Video anonymization completed for VideoFile ID: {file_id}"
                    )
                    return "video"

                except Exception as e:
                    logger.error(f"Failed to anonymize VideoFile {file_id}: {e}")
                    # Mark as failed if state exists
                    if vf.state:
                        vf.state.processing_started = (
                            False  # Mark processing as not started due to failure
                        )
                        vf.state.save(update_fields=["processing_started"])
                    raise
        if kind == "report" or kind is None:
            # Try RawPdfFile
            pdf = (
                RawPdfFile.objects.select_related("state", "sensitive_meta", "center")
                .filter(pk=file_id)
                .first()
            )
            if pdf:
                try:
                    logger.info(
                        f"Starting report processing for RawPdfFile ID: {file_id}"
                    )

                    # Check if already processed
                    if pdf.state and getattr(pdf.state, "anonymized", False):
                        logger.info(f"RawPdfFile {file_id} already processed, skipping")
                        return "pdf"

                    file_field = pdf.file
                    if not file_field or not file_field.name:
                        logger.error(f"report file not found for RawPdfFile {file_id}")
                        return None

                    if not file_exists(file_field):
                        logger.error(
                            "report file missing from storage for RawPdfFile %s",
                            file_id,
                        )
                        return None

                    # Get center name
                    center_name = pdf.center.name if pdf.center else "unknown_center"

                    # Mark as started
                    if pdf.state:
                        pdf.state.processing_started = True
                        pdf.state.save(update_fields=["processing_started"])

                    with ensure_local_file(file_field) as local_path:
                        self.pdf_service.import_and_anonymize(
                            file_path=local_path,
                            center_name=center_name,
                        )

                    logger.info(
                        f"report processing completed for RawPdfFile ID: {file_id}"
                    )
                    return "pdf"

                except Exception as e:
                    logger.error(f"Failed to process RawPdfFile {file_id}: {e}")
                    # Mark as failed if state exists
                    if pdf.state and hasattr(pdf.state, "processing_failed"):
                        pdf.state.save(update_fields=["processing_failed"])
                    elif pdf.sensitive_meta and hasattr(
                        pdf.sensitive_meta, "processing_failed"
                    ):
                        pdf.sensitive_meta.save(update_fields=["processing_failed"])
                    raise

            logger.warning(f"No file found with ID: {file_id}")
            return None

        return None

    @staticmethod
    @transaction.atomic
    def validate(file_id: int) -> None | Literal["video"] | Literal["pdf"]:
        vf = VideoFile.objects.select_related("state").filter(pk=file_id).first()
        if vf:
            video_state = vf.state or get_or_create_video_state(vf)
            if hasattr(video_state, "mark_anonymization_validated"):
                video_state.mark_anonymization_validated()
            return "video"

        pdf = RawPdfFile.objects.select_related("state").filter(pk=file_id).first()
        if pdf:
            pdf_state = pdf.state or get_or_create_raw_pdf_state(pdf)
            if hasattr(pdf_state, "mark_anonymization_validated"):
                pdf_state.mark_anonymization_validated()
            return "pdf"

        return None

    @staticmethod
    def list_items():
        video_files = VideoFile.objects.select_related("state").all()
        pdf_files = RawPdfFile.objects.select_related(
            "state"
        ).all()  # was sensitive_meta

        data = []
        for vf in video_files:
            data.append(
                {
                    "id": vf.pk,
                    "media_type": "video",
                    "anonymization_status": (
                        vf.state.anonymization_status if vf.state else "not_started"
                    ),
                    "created_at": vf.date_created,
                    "updated_at": vf.date_modified,
                }
            )

        for pdf in pdf_files:
            data.append(
                {
                    "id": pdf.pk,
                    "media_type": "pdf",
                    "anonymization_status": (
                        pdf.state.anonymization_status if pdf.state else "not_started"
                    ),
                    "created_at": pdf.date_created,
                    "updated_at": pdf.date_modified,
                }
            )
        return data
