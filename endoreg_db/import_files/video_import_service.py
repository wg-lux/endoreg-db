# endoreg_db/import_files/video_import_service.py
import logging
import shutil
from contextlib import nullcontext
from pathlib import Path
from typing import Optional, Union


from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.import_files.context import (
    ImportContext,
    content_hash_lock,
    file_lock,
)
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
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
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityError,
    MediaIntegrityExpectation,
    check_video_media_integrity,
)
from endoreg_db.services.video_files import (
    get_or_create_video_state,
    get_video_by_content_hash,
    get_video_import_context_names,
)
from endoreg_db.utils.filesystem import paths as path_utils

logger = logging.getLogger(__name__)
PIPELINE_STORAGE_MULTIPLIER = 2.5


try:
    from endoreg_db.import_files.processing.video_processing.video_anonymization import (
        VideoAnonymizer,
    )
except ImportError as exc:  # pragma: no cover - exercised by dependency-light tests
    _VIDEO_ANONYMIZER_IMPORT_ERROR = exc

    class VideoAnonymizer:  # type: ignore[no-redef]
        """Import-time placeholder so tests can monkeypatch VideoAnonymizer."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Video anonymization dependencies are unavailable"
            ) from _VIDEO_ANONYMIZER_IMPORT_ERROR


def _storage_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().storage


def _sensitive_video_dir() -> Path:
    return (
        path_utils.EndoregPathsModel.from_environment().transcoding / "sensitive_videos"
    )


def _video_import_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().import_video


def _hash_lock_dir() -> Path:
    return _storage_dir() / "locks" / "video_content"


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
            if ctx.file_hash is None:
                raise ValueError("File hash missing.")
            if not isinstance(ctx.file_hash, str):
                ctx.file_hash = str(ctx.file_hash)

            with content_hash_lock(ctx.file_hash, _hash_lock_dir()):
                logger.info("Acquired content-hash lock for %s", ctx.file_hash)
                existing_completed_video = self._get_existing_completed_video(ctx)
                if existing_completed_video is not None and not retry:
                    ctx.current_video = existing_completed_video
                    self._cleanup_duplicate_staging(ctx)
                    return existing_completed_video

                self._ensure_pipeline_storage_budget(ctx.file_path)
                ctx.sensitive_path = create_sensitive_copy(
                    ctx.file_path, _sensitive_video_dir(), ctx
                )

                # create or retrieve VideoFile + update history
                ctx.current_video, processed, needs_processing = (
                    create_or_retrieve_video_file(ctx)
                )
                get_or_create_video_state(ctx.current_video)
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
                    ctx = self.anonymizer.anonymize_video(ctx)
                    logger.info(
                        "Primary video anonymization succeeded for %s",
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

    def reanonymize_existing_video(
        self,
        video: VideoFile,
        *,
        source_path: Union[Path, str, None] = None,
    ) -> VideoFile:
        """
        Re-run anonymization for an existing VideoFile without re-import staging.

        Re-imports already have a canonical raw file attached to the VideoFile.
        Running them through import_and_anonymize again creates a new sensitive
        copy and, for videos, transcodes before anonymization. This path keeps
        the existing VideoFile/state contract while using only one local raw
        materialization as the frame-cleaning input.
        """
        video_hash = getattr(video, "video_hash", None)
        if source_path is None:
            ensure_raw = getattr(video, "ensure_local_raw_file", None)
            if not callable(ensure_raw):
                raise RuntimeError(
                    f"Video {video_hash} cannot materialize its raw file."
                )
            source_context = ensure_raw()
        else:
            source_context = nullcontext(Path(source_path))

        with source_context as local_source_path:
            if local_source_path is None:
                raise RuntimeError(f"Video {video_hash} raw source is unavailable.")

            local_source_path = Path(local_source_path)
            if not local_source_path.exists():
                raise FileNotFoundError(f"Video file not found: {local_source_path}")

            with file_lock(local_source_path):
                logger.info("Acquired file lock for re-anonymization: %s", video_hash)
                center_name, processor_name = get_video_import_context_names(video)
                ctx = ImportContext(
                    file_path=local_source_path,
                    center_name=center_name,
                    processor_name=processor_name,
                    file_type="video",
                )
                if ctx.file_hash is None:
                    raise ValueError("File hash missing.")
                if not isinstance(ctx.file_hash, str):
                    ctx.file_hash = str(ctx.file_hash)

                with content_hash_lock(ctx.file_hash, _hash_lock_dir()):
                    logger.info(
                        "Acquired content-hash lock for re-anonymization: %s",
                        ctx.file_hash,
                    )
                    ctx.original_path = local_source_path
                    ctx.local_source_path = local_source_path
                    ctx.current_video = video
                    ctx.instance = video
                    ctx.retry = True

                    try:
                        mark_instance_processing_started(video, ctx)
                        logger.info(
                            "Persisted video state as processing before re-anonymization: video=%s",
                            video_hash,
                        )
                        ctx = self.anonymizer.anonymize_video(ctx)
                        logger.info(
                            "Existing video re-anonymization succeeded for %s",
                            video_hash,
                        )
                        finalize_video_success(ctx)
                        return video
                    except Exception as exc:
                        logger.exception(
                            "Existing video re-anonymization failed for %s: %s",
                            video_hash,
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

        existing_video = get_video_by_content_hash(file_hash)
        integrity_result = check_video_media_integrity(
            existing_video,
            expectation=MediaIntegrityExpectation.RAW_WATCHER_VIDEO,
            content_hash=file_hash,
        )
        if not integrity_result.ok:
            logger.error(
                "Successful processing history exists for %s but media integrity "
                "failed before staging: %s",
                file_hash,
                integrity_result.reason,
            )
            raise MediaIntegrityError(integrity_result)

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
        storage_dir = _storage_dir()
        free_space = shutil.disk_usage(storage_dir).free
        if free_space < required_space:
            raise InsufficientStorageError(
                (
                    "Insufficient pipeline storage. "
                    f"Required: {required_space / 1e9:.1f} GB, "
                    f"Available: {free_space / 1e9:.1f} GB in {storage_dir}"
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
        safe_cleanup_staging_file(
            ctx.sensitive_path,
            label="duplicate video sensitive copy",
            missing_ok=False,
        )

        original_path = (
            ctx.original_path if isinstance(ctx.original_path, Path) else None
        )
        if (
            isinstance(original_path, Path)
            and original_path.parent.resolve() == _video_import_dir().resolve()
        ):
            safe_cleanup_staging_file(
                original_path,
                label="duplicate video import source",
                missing_ok=False,
            )
