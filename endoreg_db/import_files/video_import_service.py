# endoreg_db/services/video_import.py
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

from django.db.models.fields.files import FieldFile

from endoreg_db.import_files.context import (
    ImportContext,
    file_lock,
)
from endoreg_db.import_files.storage.state_management import (
    finalize_failure,
    finalize_video_success
)
from endoreg_db.import_files.storage.storage import create_sensitive_copy, move_to_anonymized
from endoreg_db.import_files.storage.create_video_file import (
    create_or_retrieve_video_file,
)
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.storage.state_management import (
    mark_instance_processing_started,
)
from endoreg_db.models import VideoFile
from endoreg_db.models.media.storage.processing_history import ProcessingHistory
from endoreg_db.import_files.processing.video_processing.video_anonymization import VideoAnonymizer
from endoreg_db.utils.paths import (
    ANONYM_VIDEO_DIR,
    IMPORT_VIDEO_DIR,
    SENSITIVE_VIDEO_DIR,
    TRANSCODING_DIR
)


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

    These actions are delegated to the modules inside file_import

    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.quarantine_path = Path(TRANSCODING_DIR)
        validate_directories()

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
        ctx: ImportContext = ImportContext(
            Path(file_path), center_name, processor_name, save_video, delete_source
        )
        self.quarantine_path = TRANSCODING_DIR
        ctx.sensitive_path = create_sensitive_copy(
            ctx.file_path, SENSITIVE_VIDEO_DIR
        )

        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")
        try:
            with file_lock(ctx.file_path):


                # create or retrieve VideoFile + update history
                ctx.current_video, ctx.file_hash, ctx.retry = (
                    create_or_retrieve_video_file(ctx)
                )

                mark_instance_processing_started(ctx.current_video, ctx)
                anonymizer = VideoAnonymizer()
                ctx = anonymizer.anonymize_video(ctx)
                finalize_video_success(ctx)
                
        except Exception as e:
            finalize_failure(ctx)
