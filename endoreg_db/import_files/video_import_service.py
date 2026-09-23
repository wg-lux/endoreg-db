# endoreg_db/import_files/video_import_service.py
import logging
import shutil
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
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
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.import_files.file_storage.create_video_file import (
    create_or_retrieve_video_file,
)
from endoreg_db.import_files.file_storage.state_management import (
    ensure_transferred_video_hls,
    ensure_video_hls,
    finalize_failure,
    finalize_video_success,
    mark_instance_processing_started,
)
from endoreg_db.import_files.file_storage.storage import create_sensitive_copy
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityError,
    check_video_media_integrity,
    require_reusable_video_raw_source,
    video_integrity_failure_allows_existing_video_reprocessing,
)
from endoreg_db.services.video_files import (
    ensure_local_raw_video_file,
    get_or_create_video_state,
    get_video_by_content_hash,
    get_video_import_context_names,
    initialize_video_file,
)
from endoreg_db.services.video_storage_normalization import (
    normalize_video_file,
    probe_video_artifact,
    segment_timeline_references,
)
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.workload_timing import (
    WorkloadOperation,
    WorkloadOutcome,
    WorkloadQueue,
    WorkloadTaskFamily,
    emit_workload_timing,
    retry_bucket,
    start_workload_timing,
)
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.rust_backend import stable_file_identity

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)
workload_timing_logger = logging.getLogger("endoreg_db.workload_timing")
PIPELINE_STORAGE_MULTIPLIER = 2.5
_video_import_outcome: ContextVar[WorkloadOutcome | None] = ContextVar(
    "video_import_outcome",
    default=None,
)


def _set_video_import_outcome(outcome: WorkloadOutcome) -> None:
    if _video_import_outcome.get() is not None:
        _video_import_outcome.set(outcome)


@dataclass(frozen=True)
class _RawSourceIdentity:
    size_bytes: int
    modified_time_ns: int
    sha256: str


@dataclass(frozen=True)
class VideoImportExecutionFence:
    attempt_id: str
    guard: Callable[[], None]

    def __post_init__(self) -> None:
        if not self.attempt_id.strip():
            raise ValueError("Video import execution fence requires an attempt_id")


class _VideoAnonymizer(Protocol):
    def anonymize_video(self, ctx: ImportContext) -> ImportContext: ...


class _VideoImportState(Protocol):
    anonymization_validated: bool


class _LocalRawVideo(Protocol):
    raw_video_hash: str
    original_file_name: str
    state: _VideoImportState | None


class _RawPathVideo(Protocol):
    def get_raw_file_path(self) -> Path | str | None: ...


class _LocalRawSourceProvider(Protocol):
    def __call__(self) -> AbstractContextManager[Path]: ...


VideoAnonymizer: type[_VideoAnonymizer] | None = None


def _load_video_anonymizer_class() -> type[_VideoAnonymizer]:
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
    except ImportError as exc:
        raise RuntimeError("Video anonymization dependencies are unavailable") from exc

    VideoAnonymizer = cast(type[_VideoAnonymizer], imported_video_anonymizer)
    return VideoAnonymizer


def _storage_dir() -> Path:
    return get_runtime_paths().storage


def _sensitive_video_dir() -> Path:
    return get_runtime_paths().sensitive_video


def _video_import_dir() -> Path:
    return get_runtime_paths().import_video


def local_raw_source_context(
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

    raw_video_hash = getattr(video, "raw_video_hash", "unknown")
    raise ValueError(f"Video {raw_video_hash} has no local raw source")


def _supports_reanonymization_metadata_initialization(video: VideoFile) -> bool:
    if getattr(video, "video_meta_id", None) is not None:
        return True
    return (
        getattr(video, "center_id", None) is not None
        and getattr(video, "processor_id", None) is not None
    )


def _supports_video_file_initialization(video: object) -> bool:
    return isinstance(video, VideoFile)


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


def _raw_source_identity(path: Path) -> _RawSourceIdentity:
    identity = stable_file_identity(path)
    if identity is None:
        raise RuntimeError("Native stable file identity is required for video import")
    return _RawSourceIdentity(*identity)


def _configured_reimport_transcode_quality_mode() -> str:
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
    if ctx.execution_guard is not None:
        ctx.execution_guard()


@contextmanager
def cleanup_cancelled_import_staging(ctx: ImportContext) -> Generator[None]:
    from endoreg_db.services.hub.upload_job_cancellation import (
        UploadJobCancellationCleanupFailed,
        UploadJobImportCancelled,
    )

    try:
        yield
    except UploadJobImportCancelled:
        staging_root = get_runtime_paths().transcoding
        try:
            for candidate in (ctx.anonymized_path, ctx.sensitive_path):
                if candidate is None:
                    continue
                if candidate.resolve() == ctx.file_path.resolve() or (
                    ctx.original_path is not None
                    and candidate.resolve() == ctx.original_path.resolve()
                ):
                    raise UploadJobCancellationCleanupFailed(
                        "Cancellation staging overlaps its source"
                    )
                if candidate.is_symlink():
                    raise UploadJobCancellationCleanupFailed(
                        "Cancellation staging is a symbolic link"
                    )
                if not candidate.exists():
                    continue
                if not safe_cleanup_staging_file(
                    candidate,
                    label="cancelled video import staging",
                    allowed_roots=(staging_root,),
                    missing_ok=False,
                ):
                    raise UploadJobCancellationCleanupFailed(
                        "Cancellation staging cleanup rejected"
                    )
                if candidate.exists():
                    raise UploadJobCancellationCleanupFailed(
                        "Cancellation staging remains after cleanup"
                    )
        except (OSError, UploadJobCancellationCleanupFailed) as exc:
            raise UploadJobCancellationCleanupFailed(
                "Video import staging cleanup failed"
            ) from exc
        raise


def _finalize_video_failure_if_owned(
    ctx: ImportContext,
    *,
    preserve_existing_video_artifacts: bool = False,
) -> None:
    _require_execution_ownership(ctx)
    finalize_failure(
        ctx, preserve_existing_video_artifacts=preserve_existing_video_artifacts
    )


def _normalize_reimport_video_quality(ctx: ImportContext) -> None:
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
    if (
        isinstance(ctx.current_video, VideoFile)
        and ctx.current_video.pk
        and ctx.current_video.label_video_segments.exists()
    ):
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
        getattr(ctx.current_video, "raw_video_hash", None),
        ctx.storage_normalization_evidence.profile_name,
        quality_mode,
        source_path,
    )


class VideoImportService:
    """Service for importing and anonymizing video files."""

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
    ) -> VideoFile | None:
        return self._timed_import_and_anonymize(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            retry=retry,
            execution_fence=None,
        )

    def import_and_anonymize_fenced(
        self,
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        *,
        execution_fence: VideoImportExecutionFence,
        retry: bool = False,
    ) -> VideoFile | None:
        return self._timed_import_and_anonymize(
            file_path=file_path,
            center_name=center_name,
            processor_name=processor_name,
            retry=retry,
            execution_fence=execution_fence,
        )

    def _timed_import_and_anonymize(
        self,
        *,
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        retry: bool,
        execution_fence: VideoImportExecutionFence | None,
    ) -> VideoFile | None:
        started_at = start_workload_timing()
        outcome_token = _video_import_outcome.set(WorkloadOutcome.FAILED)
        try:
            if execution_fence is not None:
                execution_fence.guard()
            result = self._import_and_anonymize(
                file_path=file_path,
                center_name=center_name,
                processor_name=processor_name,
                retry=retry,
                execution_fence=execution_fence,
            )
            if _video_import_outcome.get() is WorkloadOutcome.FAILED:
                _set_video_import_outcome(WorkloadOutcome.COMPLETED)
            return result
        except Exception:
            _set_video_import_outcome(WorkloadOutcome.FAILED)
            raise
        finally:
            outcome = _video_import_outcome.get() or WorkloadOutcome.FAILED
            try:
                emit_workload_timing(
                    workload_timing_logger,
                    started_at=started_at,
                    operation=WorkloadOperation.VIDEO_IMPORT,
                    outcome=outcome,
                    task_family=WorkloadTaskFamily.VIDEO_UPLOAD_IMPORT,
                    queue=WorkloadQueue.PIPELINE,
                    retry=retry_bucket(int(retry)),
                )
            finally:
                _video_import_outcome.reset(outcome_token)

    def _import_and_anonymize(
        self,
        *,
        file_path: Path | str,
        center_name: str,
        processor_name: str,
        retry: bool,
        execution_fence: VideoImportExecutionFence | None,
    ) -> VideoFile | None:
        """Single linear execution path for video import, staging, and anonymization."""
        context_values: dict[str, object] = {
            "file_path": Path(file_path),
            "center_name": center_name,
            "processor_name": processor_name,
            "file_type": "video",
            "defer_video_initialization": True,
            "execution_guard": (
                execution_fence.guard if execution_fence is not None else None
            ),
        }
        if execution_fence is not None:
            context_values["attempt_id"] = execution_fence.attempt_id

        ctx = ImportContext.model_validate(context_values)
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        ctx.original_path = ctx.file_path
        ctx.retry = retry

        with cleanup_cancelled_import_staging(ctx), file_lock(ctx.original_path):
            logger.info("Acquired video source lock")
            ctx.file_hash = get_file_hash(ctx.file_path)

            with content_hash_lock(ctx.file_hash):
                logger.info("Acquired content-hash lock for %s", ctx.file_hash)

                # 1. Short-circuit on reusable existing completed videos
                existing_video = self._get_existing_completed_video(ctx)
                if existing_video is not None:
                    if not retry:
                        ctx.current_video = existing_video
                        _require_execution_ownership(ctx)
                        self._ensure_duplicate_streaming(ctx, existing_video)
                        if existing_video.raw_file:
                            self._cleanup_duplicate_staging(ctx)
                        _set_video_import_outcome(WorkloadOutcome.REUSED)
                        return existing_video
                    require_reusable_video_raw_source(existing_video)

                # 2. Stage sensitive copy & obtain VideoFile instance
                _require_execution_ownership(ctx)
                self._ensure_pipeline_storage_budget(ctx.file_path)
                ctx.sensitive_path = create_sensitive_copy(
                    ctx.file_path, _sensitive_video_dir(), ctx
                )

                _require_execution_ownership(ctx)
                ctx.current_video, _, needs_processing = create_or_retrieve_video_file(
                    ctx
                )
                _require_execution_ownership(ctx)

                state = get_or_create_video_state(ctx.current_video)
                current_video = cast(_LocalRawVideo, ctx.current_video)

                # 3. Handle retry finalization reset or short-circuit if unneeded
                if retry and needs_processing and not state.anonymization_validated:
                    _finalize_video_failure_if_owned(ctx)
                    ctx.current_video, _, needs_processing = (
                        create_or_retrieve_video_file(ctx)
                    )

                if not needs_processing and not retry:
                    _require_execution_ownership(ctx)
                    self._ensure_duplicate_streaming(ctx, ctx.current_video)
                    if ctx.current_video.raw_file:
                        self._cleanup_duplicate_staging(ctx)
                    _set_video_import_outcome(WorkloadOutcome.REUSED)
                    return ctx.current_video

                # 4. Anonymize, normalize, and finalize success
                try:
                    _require_execution_ownership(ctx)
                    mark_instance_processing_started(ctx.current_video, ctx)
                    logger.info(
                        "Persisted video state as processing before anonymization: video=%s",
                        current_video.raw_video_hash,
                    )
                    with self._verified_local_raw_source(ctx):
                        ctx = self.anonymizer.anonymize_video(ctx)
                        _normalize_reimport_video_quality(ctx)

                    _require_execution_ownership(ctx)
                    logger.info(
                        "Video anonymization succeeded for content hash %s",
                        ctx.file_hash,
                    )
                    finalize_video_success(ctx)
                    return ctx.current_video

                except Exception as exc:
                    logger.exception(
                        "Video import/anonymization failed for content hash %s: %s",
                        ctx.file_hash,
                        exc,
                    )
                    try:
                        _finalize_video_failure_if_owned(ctx)
                    except Exception as ownership_exc:
                        logger.error(
                            "Skipping video failure finalization due to invalid ownership: attempt=%s error=%s",
                            ctx.attempt_id,
                            ownership_exc,
                        )
                        raise ownership_exc from exc
                    raise

    @contextmanager
    def _verified_local_raw_source(
        self,
        ctx: ImportContext,
        *,
        source_path: Path | None = None,
        initialize_metadata: bool = True,
    ) -> Generator[None]:
        assert ctx.current_video is not None

        previous_local_source = ctx.local_source_path
        fallback_path = Path(ctx.sensitive_path) if ctx.sensitive_path else None
        source_context = (
            nullcontext(source_path)
            if source_path is not None
            else local_raw_source_context(
                ctx.current_video, fallback_path=fallback_path
            )
        )
        with source_context as local_source_path:
            local_source_path = Path(local_source_path)
            before_identity = _raw_source_identity(local_source_path)
            if initialize_metadata and _supports_video_file_initialization(
                ctx.current_video
            ):
                ctx.current_video = initialize_video_file(
                    ctx.current_video,
                    local_raw_path=local_source_path,
                )
            after_identity = _raw_source_identity(local_source_path)
            if before_identity != after_identity:
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
        """Re-run anonymization for an existing VideoFile directly from its raw source."""
        raw_video_hash = getattr(video, "raw_video_hash", None)
        source_context = (
            local_raw_source_context(video)
            if source_path is None
            else nullcontext(Path(source_path))
        )

        with source_context as local_source_path:
            local_source_path = Path(local_source_path)
            if not local_source_path.exists():
                raise FileNotFoundError(f"Video file not found: {local_source_path}")

            with file_lock(local_source_path):
                logger.info(
                    "Acquired file lock for re-anonymization: %s", raw_video_hash
                )
                center_name, processor_name = get_video_import_context_names(video)
                source_identity = get_file_hash(local_source_path)

                ctx = ImportContext(
                    file_path=local_source_path,
                    center_name=center_name,
                    processor_name=processor_name,
                    file_type="video",
                    file_hash=source_identity,
                    original_path=local_source_path,
                    local_source_path=local_source_path,
                    current_video=video,
                    instance=video,
                    retry=True,
                )

                with content_hash_lock(source_identity):
                    logger.info(
                        "Acquired content-hash lock for re-anonymization: %s",
                        ctx.file_hash,
                    )

                    with self._verified_local_raw_source(
                        ctx,
                        source_path=local_source_path,
                        initialize_metadata=_supports_reanonymization_metadata_initialization(
                            video
                        ),
                    ):
                        try:
                            mark_instance_processing_started(video, ctx)
                            ctx = self.anonymizer.anonymize_video(ctx)
                            _normalize_reimport_video_quality(ctx)
                            logger.info(
                                "Existing video re-anonymization succeeded for %s",
                                raw_video_hash,
                            )
                            finalize_video_success(ctx)
                            return video
                        except Exception as exc:
                            logger.exception(
                                "Existing video re-anonymization failed for %s: %s",
                                raw_video_hash,
                                exc,
                            )
                            finalize_failure(
                                ctx, preserve_existing_video_artifacts=True
                            )
                            raise

    @staticmethod
    def _ensure_duplicate_streaming(ctx: ImportContext, video: VideoFile) -> None:
        if not video.raw_file:
            if video.center.name != ctx.center_name:
                raise ValueError("Transferred video belongs to a different center")
            ensure_transferred_video_hls(video, execution_guard=ctx.execution_guard)
        else:
            ensure_video_hls(video, execution_guard=ctx.execution_guard)

    def _get_existing_completed_video(self, ctx: ImportContext) -> VideoFile | None:
        file_hash = ctx.file_hash
        if not isinstance(file_hash, str):
            return None

        if not ProcessingHistory.has_history_for_hash(
            file_hash=file_hash, success=True
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
            if isinstance(existing_video, VideoFile):
                if not video_integrity_failure_allows_existing_video_reprocessing(
                    integrity_result
                ):
                    raise MediaIntegrityError(integrity_result)
                ctx.current_video = existing_video
            else:
                ctx.current_video = None
            logger.warning(
                "Successful processing history exists for %s but media integrity failed before staging: %s.",
                file_hash,
                integrity_result.reason,
            )
            return None

        logger.info(
            "VideoFile already has successful processing history (file_hash=%s) - short-circuiting",
            file_hash,
        )
        return existing_video

    def _ensure_pipeline_storage_budget(self, source_path: Path) -> None:
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

    def _cleanup_duplicate_staging(self, ctx: ImportContext) -> None:
        _require_execution_ownership(ctx)
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
            _require_execution_ownership(ctx)
            safe_cleanup_staging_file(
                original_path,
                label="duplicate video import source",
                missing_ok=False,
            )
