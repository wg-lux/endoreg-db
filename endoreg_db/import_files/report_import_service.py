# endoreg_db/services/report_import_service.py
from pathlib import Path
from typing import Optional, Union, Any, Dict
import logging
import os

from endoreg_db.import_files.context import ReportImportContext, _file_lock
from endoreg_db.models import RawPdfFile
from endoreg_db.utils.paths import ANONYM_VIDEO_DIR, STORAGE_DIR, VIDEO_DIR

logger = logging.getLogger(__name__)


class ReportImportService():
    """
    Service for importing and anonymizing report files.
    Uses a central report instance pattern for cleaner state management.
    Responsibility:
        Validate path
        Move/copy raw file into stable storage
        Create/attach ReportFile, ReportState, default SensitiveMeta
        Mark a single clear state like state.import_completed = True or state.ready_for_anonymization = True

    It does not:
        Run frame cleaning
        Run ML
        Create processed_file
        Decide fallback anonymization

    """

    def __init__(self, project_root: Optional[Path] = None):
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent.parent

        # Track processed files to prevent duplicates
        try:
            # Ensure anonym_report directory exists before listing files
            anonym_report_dir = Path(ANONYM_VIDEO_DIR)
            if anonym_report_dir.exists():
                self.processed_files = set(str(anonym_report_dir / file) for file in os.listdir(ANONYM_VIDEO_DIR))
            else:
                logger.info(f"Creating anonym_reports directory: {anonym_report_dir}")
                anonym_report_dir.mkdir(parents=True, exist_ok=True)
                self.processed_files = set()
        except Exception as e:
            logger.warning(f"Failed to initialize processed files tracking: {e}")
            self.processed_files = set()

        self.current_report: Optional[RawPdfFile] = None
        self.processing_context: ReportImportContext = {}  # you can keep or rely on base
        self.delete_source = True
        self.original_file_path: Optional[Path] = None

        self.logger = logging.getLogger(__name__)
        self.current_report_id: Optional[int] = None
        self.cleaner = None
    
    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_report: bool = True,
        delete_source: bool = True,
    ) -> "RawPdfFile | None":
        """
        Public entrypoint: wrap BaseImportService.import_and_anonymize.
        """
        # Set original_path for the base class (used for locking)
        self.original_path = Path(file_path)

        with _file_lock(Path(self.original_path), self._cleanup_on_error()):
            logger.info("Acquired file lock for %s", path_to_lock)

            self._validate_and_prepare_file()
            self._create_or_retrieve_instance()
            if not self._has_instance():
                logger.warning("No instance created for %s; aborting", path_to_lock)
                return None

            self._setup_processing_environment()
            self._process_payload()
            self._finalize_processing()

            # Mark as processed in this service instance (session-level guard)
            self.processed_files.add(str(Path(file_path)))
            return self._get_instance()
        
    def _validate_path() -> bool:
        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent.parent

        
    def _cleanup_on_error() -> bool:
        

