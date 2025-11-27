# endoreg_db/services/video_import.py
from pathlib import Path
from typing import Optional, Union, Any, Dict

import logging

from endoreg_db.import_files.base_import_service import VideoImportContext
from endoreg_db.models import VideoFile


class VideoImportService(BaseImportService):
    """
    Service for importing and anonymizing video files.
    Uses a central video instance pattern for cleaner state management.
    Responsibility:
        Validate path
        Move/copy raw file into stable storage
        Create/attach VideoFile, VideoState, default SensitiveMeta
        Mark a single clear state like state.import_completed = True or state.ready_for_anonymization = True

    It does not:
        Run frame cleaning
        Run ML
        Create processed_file
        Decide fallback anonymization

    """

    def __init__(self, project_root: Optional[Path] = None):
        super().__init__()  # important!

        if project_root:
            self.project_root = Path(project_root)
        else:
            self.project_root = Path(__file__).parent.parent.parent  # whatever you had

        self.current_video: Optional[VideoFile] = None
        self.processing_context: Dict[str, Any] = {}  # you can keep or rely on base
        self.delete_source = True
        self.original_file_path: Optional[Path] = None

        self.logger = logging.getLogger(__name__)
        self.current_video_id: Optional[int] = None
        self.cleaner = None
    
    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_video: bool = True,
        delete_source: bool = True,
    ) -> "VideoFile | None":
        """
        Public entrypoint: wrap BaseImportService.import_and_anonymize.
        """
        # Set original_path for the base class (used for locking)
        self.original_path = Path(file_path)

        with _file_lock(Path(path_to_lock), self._cleanup_on_error()):
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

