# endoreg_db/services/video_import.py
from pathlib import Path
from typing import Optional, Union, Any, Dict

import logging

from endoreg_db.import_files.base_import_service import BaseImportService
from endoreg_db.models import VideoFile


class VideoImportService(BaseImportService):
    """
    Service for importing and anonymizing video files.
    Uses a central video instance pattern for cleaner state management.
    """

    def __init__(self, project_root: Optional[Path] = None):
        super().__init__()  # important!

        # existing stuff from your old __init__
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

        return super().import_and_anonymize(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            save_video=save_video,
            delete_source=delete_source,
        )

