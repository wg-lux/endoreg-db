# endoreg_db/services/video_import.py
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

from django.db.models.fields.files import FieldFile

from endoreg_db.import_files.context import (
    ImportContext,
    file_lock,
    quarantine,
    unquarantine,
)
from endoreg_db.import_files.processing.video_processing.video_cleanup_on_error import (
    cleanup_video_on_error,
)
from endoreg_db.import_files.storage import create_sensitive_copy, move_to_anonymized
from endoreg_db.import_files.storage.create_video_file import (
    create_or_retrieve_video_file,
)
from endoreg_db.import_files.storage.state_management import mark_instance_processing_started
from endoreg_db.models import VideoFile
from endoreg_db.models.media.storage.processing_history import ProcessingHistory
from endoreg_db.utils.paths import ANONYM_VIDEO_DIR, STORAGE_DIR, VIDEO_DIR


class VideoImportService:
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

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.quarantine_path = Path(STORAGE_DIR / "_processing")

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_video: bool = True,
        delete_source: bool = True,
    ) -> "VideoFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        self.ctx: ImportContext = ImportContext(
            Path(file_path), center_name, processor_name, save_video, delete_source
        )
        self.quarantine_path = STORAGE_DIR / "quarantine"
        self.ctx.sensitive_path = create_sensitive_copy(
            self.ctx.file_path, SENSITIVE_VIDEO_DIR
        )

        self.logger.info("validating and preparing file")
        if not self.ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")
        with file_lock(self.ctx.file_path):
            self.ctx.quarantine_path = quarantine(
                self.ctx.file_path, self.quarantine_path
            )

            # create or retrieve RawPdfFile + update history
            video, file_hash, retry = create_or_retrieve_video_file(
                self.ctx
            )
            
            mark_instance_processing_started(video, self.ctx)


            self.ctx.current_video = video
            self.ctx.file_hash = file_hash
            self.ctx.retry = retry

            # ... here continue with: OCR / anonymization / saving etc. ...

            # when everything finishes successfully, mark success in history:
            ProcessingHistory.get_or_create_history(
                object_id=self.ctx.current_video.pk,
                file_hash=self.ctx.file_hash,
                success=True,
            )
