# endoreg_db/import_files/video_import_service.py
import logging
import shutil
from contextlib import AbstractContextManager, contextmanager, nullcontext
from collections.abc import Callable, Generator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from endoreg_db.config.env import (
    FFMPEG_TRANSCODE_QUALITY_MODES,
    get_ffmpeg_transcode_quality_mode,
)
from endoreg_db.exceptions import InsufficientStorageError
from endoreg_db.import_files.context import (
    content_hash_lock,
    file_lock,
)
from endoreg_db.import_files.context.import_context import (
    ImportContext,
    SourceStreamData,
)
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.import_files.file_storage.state_management import (
    ensure_video_hls,
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
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.hub.media_integrity import (
    check_video_media_integrity,
    video_integrity_failure_allows_existing_video_reprocessing,
)
from endoreg_db.services.video_files import (
    get_or_create_video_state,
    get_video_by_content_hash,
    get_video_import_context_names,
    ensure_local_raw_video_file,
    initialize_video_file,
)
from endoreg_db.services.video_storage_normalization import (
    normalize_video_file,
    probe_video_artifact,
    segment_timeline_references,
)
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import (
    sha256_file,
)
from endoreg_db.utils.rust_backend import stable_file_identity

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)
PIPELINE_STORAGE_MULTIPLIER = 2.5


@dataclass(frozen=True)
class _RawSourceIdentity:
    size_bytes: int
    modified_time_ns: int
    sha256: str


class _VideoAnonymizer(Protocol):
    def anonymize_video(self, ctx: ImportContext) -> ImportContext: ...


class _VideoImportState(Protocol):
    anonymization_validated: bool


class _LocalRawVideo(Protocol):
    video_hash: str
    original_file_name: str
    state: _VideoImportState | None


class _RawPathVideo(Protocol):
    def get_raw_file_path(self) -> Path | str | None: ...


class _LocalRawSourceProvider(Protocol):
    def __call__(self) -> AbstractContextManager[Path]: ...


VideoAnonymizer: type[_VideoAnonymizer] | None = None


def _load_video_anonymizer_class() -> type[_VideoAnonymizer]:
    """
    This avoids reloading anonymizer and repeatedly pulling in dependencies.
    """
    global VideoAnonymizer
    if VideoAnonymizer is not None:
        return VideoAnonymizer

    configured_capabilities = getattr(
        settings,
        "LX_ANONYMIZER_REQUIRED_NATIVE_CAPABILITIES",
        (),
    )
    if isinstance(configured_capabilities, str):
        required_capabilities = tuple(
            item.strip() for item in configured_capabilities.split(",") if item.strip()
        )
    else:
        required_capabilities = tuple(str(item) for item in configured_capabilities)
    if required_capabilities:
        try:
            native_module = import_module("lx_anonymizer._native")
            require_native_capabilities = cast(
                Callable[[tuple[str, ...]], None],
                getattr(native_module, "require_native_capabilities"),
            )
        except (AttributeError, ImportError) as exc:
            raise RuntimeError(
                "Required lx-anonymizer native capability contract is unavailable"
            ) from exc
        require_native_capabilities(required_capabilities)

    try:
        from endoreg_db.import_files.processing.video_processing.video_anonymization import (
            VideoAnonymizer as imported_video_anonymizer,
        )
    except ImportError as exc:  # pragma: no cover - exercised by dependency-light tests
        raise RuntimeError("Video anonymization dependencies are unavailable") from exc

    VideoAnonymizer = cast(type[_VideoAnonymizer], imported_video_anonymizer)
    return VideoAnonymizer


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


def _local_raw_source_context(
    video: object, *, fallback_path: Path | None = None
) -> AbstractContextManager[Path]:
    provider_name = "ensure_local_raw_file"
    instance_provider = vars(video).get(provider_name)
    if callable(instance_provider):
        return cast(_LocalRawSourceProvider, instance_provider)()

    if isinstance(video, VideoFile):
        return ensure_local_raw_video_file(video)

    provider = getattr(video, provider_name, None)
    if callable(provider):
        return cast(_LocalRawSourceProvider, provider)()

    get_raw_file_path = getattr(video, "get_raw_file_path", None)
    if callable(get_raw_file_path):
        raw_path = cast(_RawPathVideo, video).get_raw_file_path()
        if raw_path is not None:
            local_raw_path = Path(raw_path)
            if local_raw_path.exists() or fallback_path is None:
                return nullcontext(local_raw_path)

    if fallback_path is not None:
        return nullcontext(fallback_path)

    video_hash = getattr(video, "video_hash", "unknown")
    raise ValueError(f"Video {video_hash} has no local raw source")


def _supports_video_file_initialization(video: object) -> bool:
    return isinstance(video, VideoFile)


def _supports_reanonymization_metadata_initialization(video: VideoFile) -> bool:
    if getattr(video, "video_meta_id", None) is not None:
        return True
    return (
        getattr(video, "center_id", None) is not None
        and getattr(video, "processor_id", None) is not None
    )


def _video_meta_stream_contract(video: VideoFile | None) -> SourceStreamData:
    if video is None:
        return {}
    contract: SourceStreamData = {}
    width = getattr(video, "width", None)
    if isinstance(width, int) and not isinstance(width, bool):
        contract["width"] = width
    height = getattr(video, "height", None)
    if isinstance(height, int) and not isinstance(height, bool):
        contract["height"] = height
    fps = getattr(video, "fps", None)
    if isinstance(fps, (int, float)) and not isinstance(fps, bool):
        contract["fps"] = float(fps)
    duration = getattr(video, "duration", None)
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        contract["duration"] = float(duration)
    frame_count = getattr(video, "frame_count", None)
    if isinstance(frame_count, int) and not isinstance(frame_count, bool):
        contract["frame_count"] = frame_count
    return contract


def _record_validated_raw_source(
    ctx: ImportContext,
    local_source_path: Path,
    identity: _RawSourceIdentity,
) -> None:
    local_source_path = Path(local_source_path)
    if not local_source_path.exists():
        raise FileNotFoundError(f"Video raw source not found: {local_source_path}")
    if not local_source_path.is_file():
        raise RuntimeError(
            f"Video raw source is not a regular file: {local_source_path}"
        )

    if identity.size_bytes <= 0:
        raise RuntimeError(f"Video raw source is empty: {local_source_path}")

    ctx.validated_raw_source_path = local_source_path.resolve()
    ctx.validated_raw_source_size_bytes = identity.size_bytes
    ctx.validated_raw_source_mtime_ns = identity.modified_time_ns
    ctx.validated_raw_source_sha256 = identity.sha256
    ctx.validated_raw_source_stream = _video_meta_stream_contract(ctx.current_video)
    logger.info(
        "Validated local raw source for anonymization: video=%s path=%s "
        "size_bytes=%s sha256=%s width=%s height=%s",
        getattr(ctx.current_video, "video_hash", None),
        ctx.validated_raw_source_path,
        ctx.validated_raw_source_size_bytes,
        ctx.validated_raw_source_sha256,
        ctx.validated_raw_source_stream.get("width"),
        ctx.validated_raw_source_stream.get("height"),
    )


def _raw_source_identity(local_source_path: Path) -> _RawSourceIdentity:
    local_source_path = Path(local_source_path)
    native_identity = stable_file_identity(local_source_path)
    if native_identity is not None:
        return _RawSourceIdentity(*native_identity)

    before = local_source_path.stat()
    source_sha256 = sha256_file(local_source_path)
    after = local_source_path.stat()
    before_metadata = (
        int(before.st_dev),
        int(before.st_ino),
        int(before.st_size),
        int(before.st_mtime_ns),
    )
    after_metadata = (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
    )
    if before_metadata != after_metadata:
        raise RuntimeError(
            f"Video raw source changed while deriving stable identity: "
            f"{local_source_path}"
        )
    return _RawSourceIdentity(
        size_bytes=int(after.st_size),
        modified_time_ns=int(after.st_mtime_ns),
        sha256=source_sha256,
    )


def _configured_reimport_transcode_quality_mode() -> str:
    """Resolve the host setting while retaining the endoreg default."""
    try:
        configured = getattr(settings, "FFMPEG_TRANSCODE_QUALITY_MODE", None)
    except ImproperlyConfigured:
        configured = None

    if configured is None:
        return get_ffmpeg_transcode_quality_mode()

    quality_mode = str(configured).strip().lower()
    if quality_mode not in FFMPEG_TRANSCODE_QUALITY_MODES:
        allowed = ", ".join(sorted(FFMPEG_TRANSCODE_QUALITY_MODES))
        raise ValueError(f"FFMPEG_TRANSCODE_QUALITY_MODE must be one of: {allowed}")
    return quality_mode


def _require_execution_ownership(ctx: ImportContext) -> None:
    """Fail before a durable mutation when the caller no longer owns the attempt."""
    if ctx.execution_guard is not None:
        ctx.execution_guard()


def _finalize_video_failure_if_owned(
    ctx: ImportContext,
    *,
    preserve_existing_video_artifacts: bool = False,
) -> None:
    """Never let a superseded worker reset state or delete shared artifacts."""
    _require_execution_ownership(ctx)
    if preserve_existing_video_artifacts:
        finalize_failure(ctx, preserve_existing_video_artifacts=True)
    else:
        finalize_failure(ctx)


def _normalize_reimport_video_quality(ctx: ImportContext) -> None:
    """Normalize a fresh anonymized output against the stored source timeline."""
    source_path = ctx.anonymized_path
    if not isinstance(source_path, Path):
        raise RuntimeError(
            "Cannot normalize re-import quality without an anonymized video output."
        )
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise RuntimeError(
            f"Cannot normalize missing or empty re-import output: {source_path}"
        )

    reference_path = ctx.validated_raw_source_path
    if not isinstance(reference_path, Path) or not reference_path.is_file():
        raise RuntimeError(
            "Cannot normalize video storage without the validated raw source."
        )

    segments = []
    if isinstance(ctx.current_video, VideoFile) and ctx.current_video.pk:
        segments = segment_timeline_references(
            ctx.current_video,
            timeline=probe_video_artifact(reference_path).timeline,
        )

    quality_mode = _configured_reimport_transcode_quality_mode()
    ctx.storage_normalization_evidence = normalize_video_file(
        input_path=source_path,
        reference_path=reference_path,
        quality_mode=quality_mode,
        segments=segments,
    )
    logger.info(
        "Normalized video storage: video=%s profile=%s quality_mode=%s path=%s",
        getattr(ctx.current_video, "video_hash", None),
        ctx.storage_normalization_evidence.profile_name,
        quality_mode,
        source_path,
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

    def __init__(self, anonymizer: _VideoAnonymizer | None = None) -> None:
        self.logger = logger
        self._anonymizer = anonymizer
        self.processing_context: ImportContext | None = None
        self.current_video: VideoFile | None = None

        validate_directories()

    @property
    def anonymizer(self) -> _VideoAnonymizer:
        if self._anonymizer is None:
            self._anonymizer = _load_video_anonymizer_class()()
        return self._anonymizer

    @anonymizer.setter
    def anonymizer(self, value: _VideoAnonymizer) -> None:
        self._anonymizer = value

    def import_and_anonymize(
        self,
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        retry: bool = False,
        *,
        attempt_id: str | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> "VideoFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        # First, initialize import context. this will be updated during import and keep track of current paths, file type and center and processor.
        context_values: dict[str, object] = {
            "file_path": Path(file_path),
            "center_name": center_name,
            "processor_name": processor_name,
            "file_type": "video",
            "defer_video_initialization": True,
            "execution_guard": execution_guard,
        }
        if attempt_id is not None:
            context_values["attempt_id"] = attempt_id
        ctx = ImportContext.model_validate(context_values)
        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        ctx.original_path = ctx.file_path
        lock_path = ctx.original_path

        with file_lock(lock_path):
            logger.info("Acquired file lock for %s", lock_path)
            ctx.file_hash = _raw_source_identity(ctx.file_path).sha256
            with content_hash_lock(ctx.file_hash, _hash_lock_dir()):
                logger.info("Acquired content-hash lock for %s", ctx.file_hash)
                existing_completed_video = self._get_existing_completed_video(ctx)
                if existing_completed_video is not None and not retry:
                    ctx.current_video = existing_completed_video
                    _require_execution_ownership(ctx)
                    ensure_video_hls(existing_completed_video)
                    self._cleanup_duplicate_staging(ctx)
                    return existing_completed_video

                _require_execution_ownership(ctx)
                self._ensure_pipeline_storage_budget(ctx.file_path)
                ctx.sensitive_path = create_sensitive_copy(
                    ctx.file_path, _sensitive_video_dir(), ctx
                )

                # create or retrieve VideoFile + update history
                _require_execution_ownership(ctx)
                ctx.current_video, _processed, needs_processing = (
                    create_or_retrieve_video_file(ctx)
                )
                _require_execution_ownership(ctx)
                get_or_create_video_state(ctx.current_video)
                current_video = cast(_LocalRawVideo, ctx.current_video)
                if current_video.state is None:
                    raise ValueError(
                        f"{current_video.original_file_name} has no video state after trying."
                    )
                current_state = current_video.state
                ctx.current_video = ctx.current_video

                ctx.retry = retry
                # Retry is a forced overwrite of needs processing - therefore the retry will cause full deletion of processed files using finalize failure.

                if (
                    retry
                    and needs_processing
                    and not current_state.anonymization_validated
                ):
                    _finalize_video_failure_if_owned(ctx)
                    ctx.current_video, _processed, needs_processing = (
                        create_or_retrieve_video_file(ctx)
                    )

                if not needs_processing and not retry:
                    _require_execution_ownership(ctx)
                    ensure_video_hls(ctx.current_video)
                    self._cleanup_duplicate_staging(ctx)
                    return ctx.current_video

                try:
                    _require_execution_ownership(ctx)
                    mark_instance_processing_started(ctx.current_video, ctx)
                    logger.info(
                        "Persisted video state as processing before anonymization: video=%s",
                        current_video.video_hash,
                    )
                    with self._verified_local_raw_source(ctx):
                        ctx = self.anonymizer.anonymize_video(ctx)
                        _normalize_reimport_video_quality(ctx)
                    _require_execution_ownership(ctx)
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
                    try:
                        _finalize_video_failure_if_owned(ctx)
                    except Exception as ownership_exc:
                        logger.error(
                            "Skipping video failure finalization because execution "
                            "ownership is no longer valid: attempt=%s error=%s",
                            ctx.attempt_id,
                            ownership_exc,
                        )
                        raise ownership_exc from exc
                    raise

    @contextmanager
    def _verified_local_raw_source(self, ctx: ImportContext) -> Generator[None]:
        assert ctx.current_video is not None
        current_video = cast(_LocalRawVideo, ctx.current_video)

        previous_local_source = ctx.local_source_path
        fallback_path = Path(ctx.sensitive_path) if ctx.sensitive_path else None
        with _local_raw_source_context(
            ctx.current_video, fallback_path=fallback_path
        ) as local_source_path:
            local_source_path = Path(local_source_path)
            before_identity = _raw_source_identity(local_source_path)
            if _supports_video_file_initialization(ctx.current_video):
                ctx.current_video = initialize_video_file(
                    ctx.current_video,
                    local_raw_path=local_source_path,
                )
            after_identity = _raw_source_identity(local_source_path)
            if before_identity != after_identity:
                logger.critical(
                    "Video raw source changed during metadata extraction: "
                    "video=%s path=%s before=%s after=%s",
                    current_video.video_hash,
                    local_source_path.resolve(),
                    before_identity,
                    after_identity,
                )
                raise RuntimeError(
                    "Video raw source changed during VideoMeta extraction."
                )
            _record_validated_raw_source(ctx, local_source_path, after_identity)
            ctx.local_source_path = local_source_path
            try:
                yield
            finally:
                ctx.local_source_path = previous_local_source

    def reanonymize_existing_video(
        self,
        video: VideoFile,
        *,
        source_path: Path | str | None = None,
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
            source_context = _local_raw_source_context(video)
        else:
            source_context = nullcontext(Path(source_path))

        with source_context as local_source_path:
            local_source_path = Path(local_source_path)
            if not local_source_path.exists():
                raise FileNotFoundError(f"Video file not found: {local_source_path}")

            with file_lock(local_source_path):
                logger.info("Acquired file lock for re-anonymization: %s", video_hash)
                center_name, processor_name = get_video_import_context_names(video)
                source_identity = _raw_source_identity(local_source_path)
                source_hash = source_identity.sha256
                ctx = ImportContext(
                    file_path=local_source_path,
                    center_name=center_name,
                    processor_name=processor_name,
                    file_type="video",
                    file_hash=source_hash,
                )

                with content_hash_lock(source_hash, _hash_lock_dir()):
                    logger.info(
                        "Acquired content-hash lock for re-anonymization: %s",
                        ctx.file_hash,
                    )
                    ctx.original_path = local_source_path
                    ctx.local_source_path = local_source_path
                    ctx.current_video = video
                    ctx.instance = video
                    ctx.retry = True
                    before_identity = _raw_source_identity(local_source_path)
                    if _supports_reanonymization_metadata_initialization(video):
                        ctx.current_video = initialize_video_file(
                            video,
                            local_raw_path=local_source_path,
                        )
                    after_identity = _raw_source_identity(local_source_path)
                    if before_identity != after_identity:
                        logger.critical(
                            "Video raw source changed during re-anonymization "
                            "metadata extraction: video=%s path=%s before=%s after=%s",
                            video_hash,
                            local_source_path.resolve(),
                            before_identity,
                            after_identity,
                        )
                        raise RuntimeError(
                            "Video raw source changed during VideoMeta extraction."
                        )
                    _record_validated_raw_source(ctx, local_source_path, after_identity)

                    try:
                        mark_instance_processing_started(video, ctx)
                        logger.info(
                            "Persisted video state as processing before re-anonymization: video=%s",
                            video_hash,
                        )
                        ctx = self.anonymizer.anonymize_video(ctx)
                        # CRF/CQ cannot be recovered reliably from ordinary ffprobe
                        # metadata. A fresh anonymizer output therefore has no trusted
                        # proof that it matches the configured storage quality, so
                        # normalize it before replacing the canonical processed file.
                        _normalize_reimport_video_quality(ctx)
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
                        finalize_failure(
                            ctx,
                            preserve_existing_video_artifacts=True,
                        )
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

        try:
            existing_video = get_video_by_content_hash(file_hash)
        except VideoFile.DoesNotExist:
            existing_video = None

        integrity_result = check_video_media_integrity(
            existing_video if isinstance(existing_video, VideoFile) else None,
            content_hash=file_hash,
        )
        if not integrity_result.ok:
            if isinstance(
                existing_video, VideoFile
            ) and video_integrity_failure_allows_existing_video_reprocessing(
                integrity_result
            ):
                ctx.current_video = existing_video
            else:
                ctx.current_video = None
            logger.warning(
                "Successful processing history exists for %s but media integrity "
                "failed before staging: %s. Continuing reimport so the processed "
                "artifact can be repaired.",
                file_hash,
                integrity_result.reason,
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
