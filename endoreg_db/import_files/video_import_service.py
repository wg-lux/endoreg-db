# endoreg_db/import_files/video_import_service.py
import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Union


from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.import_files.context import (
    ImportContext,
    content_hash_lock,
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
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.import_files.processing.video_processing.video_anonymization import (
    VideoAnonymizer,
)
from endoreg_db.utils.paths import (
    STORAGE_DIR,
    IMPORT_VIDEO_DIR,
    SENSITIVE_VIDEO_DIR,
)


logger = logging.getLogger(__name__)
HASH_LOCK_DIR = STORAGE_DIR / "locks" / "video_content"
PIPELINE_STORAGE_MULTIPLIER = float(
    os.environ.get("VIDEO_PIPELINE_STORAGE_MULTIPLIER", "2.5")
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

    def __init__(self, anonymizer: VideoAnonymizer | None = None):
        self.logger = logger
        self._anonymizer = anonymizer
        self.processing_context: Optional[ImportContext] = None
        self.current_video: Optional[VideoFile] = None

        validate_directories()

    @property
    def anonymizer(self) -> VideoAnonymizer:
        if self._anonymizer is None:
            self._anonymizer = VideoAnonymizer()
        return self._anonymizer

    @anonymizer.setter
    def anonymizer(self, value: VideoAnonymizer) -> None:
        self._anonymizer = value

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        retry: bool = False,
    ) -> "VideoFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        # First, initialize import context. this will be updated during import and keep track of current paths, file type and center and processor.
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            processor_name=processor_name,
            file_type="video",
        )
        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        ctx.original_path = ctx.file_path
        lock_path = ctx.original_path

        with file_lock(lock_path):
            logger.info("Acquired file lock for %s", lock_path)
            if not isinstance(ctx.file_hash, str):
                ctx.file_hash = str(ctx.file_hash)
            if ctx.file_hash is None:
                raise ValueError("File hash missing.")

            with content_hash_lock(ctx.file_hash, HASH_LOCK_DIR):
                logger.info("Acquired content-hash lock for %s", ctx.file_hash)
                existing_completed_video = self._get_existing_completed_video(ctx)
                if existing_completed_video is not None and not retry:
                    ctx.current_video = existing_completed_video
                    return existing_completed_video

                self._ensure_pipeline_storage_budget(ctx.file_path)
                ctx.sensitive_path = create_sensitive_copy(
                    ctx.file_path, SENSITIVE_VIDEO_DIR
                )

                # create or retrieve VideoFile + update history
                ctx.current_video, processed, needs_processing = (
                    create_or_retrieve_video_file(ctx)
                )
                ctx.current_video.get_or_create_state()
                if ctx.current_video.state is None:
                    raise ValueError(
                        f"{ctx.current_video.original_file_name} has no video state after trying."
                    )
                ctx.current_video = ctx.current_video

                ctx.retry = retry
                # Retry is a forced overwrite of needs processing - therefore the retry will cause full deletion of processed files using finalize failure.
                current_state = ctx.current_video.state if ctx.current_video else None

                if (
                    retry
                    and needs_processing
                    and ctx
                    and ctx.current_video
                    and current_state is not None
                    and not current_state.anonymization_validated
                ):
                    finalize_failure(ctx)
                    ctx.current_video, processed, needs_processing = (
                        create_or_retrieve_video_file(ctx)
                    )

                if not needs_processing and not retry:
                    self._cleanup_duplicate_staging(ctx)
                    return ctx.current_video

                try:
                    mark_instance_processing_started(ctx.current_video, ctx)
                    logger.info(
                        "Persisted video state as processing before anonymization: video=%s",
                        ctx.current_video.video_hash,
                    )
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
                    logger.info(
                        f"Anonymized Video is located at: {ctx.anonymized_path}"
                    )

                    # --- Finalize success: history + move anonymized file ---
                    finalize_video_success(ctx)

                    return ctx.current_video

                except Exception as exc:
                    logger.exception(
                        "Video import/anonymization failed for %s: %s",
                        ctx.file_path,
                        exc,
                    )
                    finalize_failure(ctx)
                    raise

    def _get_existing_completed_video(self, ctx: ImportContext) -> VideoFile | None:
        """
        Return an already-successful video for this content hash, if one exists.

        This check runs under the content-hash lock so duplicate-content uploads can
        short-circuit before any new staging or storage reservation occurs.
        """
        file_hash = ctx.file_hash
        if not isinstance(file_hash, str):
            return None

        if not ProcessingHistory.has_history_for_hash(
            file_hash=file_hash,
            success=True,
        ):
            return None

        existing_video = VideoFile.get_video_by_content_hash(file_hash)
        if existing_video is None:
            logger.warning(
                "Successful processing history exists for %s but no VideoFile was found.",
                file_hash,
            )
            return None

        logger.info(
            "VideoFile already has successful processing history (file_hash=%s) - short-circuiting before staging",
            file_hash,
        )
        return existing_video

    def _ensure_pipeline_storage_budget(self, source_path: Path) -> None:
        """
        Reserve enough free space for raw + anonymized + temp overhead.

        This check runs once under the content-hash lock so duplicate-content uploads
        do not all race through expensive work before discovering disk pressure.
        """
        source_size = source_path.stat().st_size
        required_space = int(source_size * PIPELINE_STORAGE_MULTIPLIER)
        free_space = shutil.disk_usage(STORAGE_DIR).free
        if free_space < required_space:
            raise InsufficientStorageError(
                (
                    "Insufficient pipeline storage. "
                    f"Required: {required_space / 1e9:.1f} GB, "
                    f"Available: {free_space / 1e9:.1f} GB in {STORAGE_DIR}"
                ),
                required_space=required_space,
                available_space=free_space,
            )
        logger.info(
            "Pipeline storage check passed: %.1f GB available, %.1f GB required",
            free_space / 1e9,
            required_space / 1e9,
        )

    def _cleanup_duplicate_staging(self, ctx: ImportContext) -> None:
        """Remove duplicate staging files without touching canonical managed assets."""
        current_video = ctx.current_video
        raw_path = None
        if current_video is not None:
            raw_path = current_video.get_raw_file_path()
            if isinstance(raw_path, str):
                raw_path = Path(raw_path)

        def _safe_unlink(path: Path | None, *, label: str) -> None:
            if not isinstance(path, Path) or not path.exists():
                return
            try:
                if raw_path is not None and path.resolve() == raw_path.resolve():
                    return
            except FileNotFoundError:
                return
            try:
                path.unlink()
                logger.info("Deleted duplicate %s after short-circuit: %s", label, path)
            except Exception as exc:
                logger.warning(
                    "Could not delete duplicate %s after short-circuit %s: %s",
                    label,
                    path,
                    exc,
                )

        _safe_unlink(ctx.sensitive_path, label="sensitive copy")

        original_path = (
            ctx.original_path if isinstance(ctx.original_path, Path) else None
        )
        if isinstance(original_path, Path) and original_path.parent == IMPORT_VIDEO_DIR:
            _safe_unlink(original_path, label="import source")
