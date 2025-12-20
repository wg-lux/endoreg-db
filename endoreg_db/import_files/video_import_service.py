# endoreg_db/services/video_import.py
import logging
from pathlib import Path
from typing import Optional, Union


from endoreg_db.import_files.context import (
    ImportContext,
    file_lock,
)
from endoreg_db.import_files.file_storage.state_management import (
    finalize_failure,
    finalize_video_success,
)
from endoreg_db.import_files.file_storage.storage import create_sensitive_copy
from endoreg_db.import_files.file_storage.create_video_file import (
    create_or_retrieve_video_file,
)
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.file_storage.state_management import (
    mark_instance_processing_started,
)
from endoreg_db.models import VideoFile
from endoreg_db.import_files.processing.video_processing.video_anonymization import (
    VideoAnonymizer,
)
from endoreg_db.utils.paths import (
    SENSITIVE_VIDEO_DIR,
)


logger = logging.getLogger(__name__)


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
        self.logger = logger
        self.anonymizer = VideoAnonymizer()
        self.processing_context: Optional[ImportContext] = None
        self.current_video: Optional[VideoFile] = None

        validate_directories()

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        retry: bool = False,
        delete_source: bool = True,
    ) -> "VideoFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        # First, initialize import context. this will be updated during import and keep track of current paths, file type and center and processor.
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
            file_type="video",
        )
        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        ctx.sensitive_path = create_sensitive_copy(ctx.file_path, SENSITIVE_VIDEO_DIR)

        with file_lock(ctx.file_path):
            logger.info("Acquired file lock for %s", ctx.file_path)

            # create or retrieve VideoFile + update history
            ctx.current_video, processed, needs_processing = (
                create_or_retrieve_video_file(ctx)
            )
            ctx.current_video.get_or_create_state()
            assert ctx.current_video.state is not None
            ctx.current_video = ctx.current_video

            ctx.retry = retry
            # Retry is a forced overwrite of needs processing - therefore the retry will cause full deletion of processed files using finalize failure.

            if (
                retry
                and needs_processing
                and not ctx.current_video.state.anonymization_validated
            ):
                finalize_failure(ctx)
                ctx.current_video, processed, needs_processing = (
                    create_or_retrieve_video_file(ctx)
                )
                assert needs_processing is True
            elif not needs_processing and not retry:
                return ctx.current_video

            mark_instance_processing_started(ctx.current_video, ctx)
            try:
                # --- Anonymization with fallback ---
                try:
                    ctx = self.anonymizer.anonymize_video(ctx)
                    logger.info(
                        "Primary video anonymization succeeded for %s",
                        ctx.file_path,
                    )
                except Exception as primary_exc:
                    logger.exception(
                        "Primary video anonymization failed for %s: %s "
                        "- trying basic anonymization",
                        ctx.file_path,
                        primary_exc,
                    )
                    try:
                        ctx = self.anonymizer.anonymize_video(ctx)
                    except Exception as e:
                        logger.error(
                            f"Video Extraction failed for the second time. {e}"
                        )
                        raise
                    logger.info(
                        "Secondary video anonymization succeeded for %s",
                        ctx.file_path,
                    )
                logger.info(f"Anonymized Video is located at: {ctx.anonymized_path}")

                # --- Finalize success: history + move anonymized file ---
                finalize_video_success(ctx)

                return ctx.current_video

            except Exception as exc:
                logger.exception(
                    "Video import/anonymization failed for %s: %s", ctx.file_path, exc
                )
                finalize_failure(ctx)
                raise
