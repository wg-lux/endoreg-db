import logging
import os
from pathlib import Path
from typing import Protocol, cast

from django.db import transaction
from django.db.models.fields.files import FieldFile
from lx_dtypes.models.contracts.media_streaming import validate_ffmpeg_stream_info

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.models.state.raw_pdf import RawPdfState
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.hls_media import materialize_video_hls
from endoreg_db.services.raw_pdf_files.integrity import (
    verify_and_persist_processed_report_sha256,
)
from endoreg_db.services.video_storage_normalization import evidence_as_json
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.ffmpeg_wrapper import get_stream_info
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    atomic_move_path,
    safe_rmtree,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.storage_profile import PayloadKind, requires_app_encrypted_storage

logger = logging.getLogger(__name__)


class _ProcessableState(Protocol):
    processing_started: bool

    def mark_processing_started(self) -> None: ...

    def mark_processing_not_started(self) -> None: ...

    def mark_processing_failed(self) -> None: ...

    def mark_anonymized(self) -> None: ...

    def mark_sensitive_meta_processed(self) -> None: ...

    def save(self, *args: object, **kwargs: object) -> None: ...


class _StatefulImportInstance(Protocol):
    pk: int
    state: RawPdfState | VideoState | None

    def get_or_create_state(self) -> RawPdfState | VideoState: ...

    def save(self, *args: object, **kwargs: object) -> None: ...


def _processed_report_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().anonym_report


def _processed_video_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().anonym_video


def _verify_final_video_output(path: Path) -> None:
    """Fail finalization if the committed anonymized video is not probeable."""
    if not path.exists():
        raise RuntimeError(f"Final anonymized video missing: {path}")
    raw_stream_info = get_stream_info(path)
    if raw_stream_info is None:
        raise RuntimeError(f"Final anonymized video failed ffprobe validation: {path}")
    stream_info = validate_ffmpeg_stream_info(raw_stream_info)
    if not stream_info.has_video_stream:
        raise RuntimeError(f"Final anonymized video has no video stream: {path}")


def _require_execution_ownership(ctx: ImportContext) -> None:
    """Reject a superseded import attempt at a durable publication boundary."""
    if ctx.execution_guard is not None:
        ctx.execution_guard()


def _record_successful_video_processing_history(ctx: ImportContext) -> None:
    """Persist the success receipt while the current attempt still owns execution."""
    _require_execution_ownership(ctx)
    with transaction.atomic():
        if not isinstance(ctx.file_hash, str):
            ctx.file_hash = sha256_file(ctx.file_path)
        ProcessingHistory.get_or_create_for_hash(
            file_hash=ctx.file_hash,
            success=True,
        )


def _store_existing_final_file(
    field_file: FieldFile,
    final_path: Path,
    *,
    relative_name: str | None = None,
) -> str:
    """
    Attach an already-written local file to a FileField without leaving plaintext.

    When the field storage is encrypted and the file already occupies its target
    storage path, encrypt it in place to preserve the canonical filename.
    """
    relative_name = relative_name or path_utils.to_storage_relative(final_path)
    field_file.name = relative_name
    storage = getattr(field_file, "storage", None)
    repair_plaintext_file = getattr(storage, "repair_plaintext_file", None)
    storage_path = None
    try:
        storage_path = Path(storage.path(relative_name)).resolve() if storage else None
    except Exception:
        storage_path = None
    if storage_path is not None and final_path.resolve() == storage_path:
        if callable(repair_plaintext_file):
            repair_plaintext_file(relative_name)
        elif any(
            hasattr(storage, attr)
            for attr in ("open_encrypted", "iter_decrypted_range", "get_plaintext_size")
        ):
            raise RuntimeError(
                "Cannot attach plaintext directly to encrypted FieldFile storage "
                f"without a repair hook: {relative_name}"
            )
        return relative_name
    return save_local_file(
        field_file,
        final_path,
        name=relative_name,
        save=False,
        overwrite=True,
    )


def ensure_video_hls(
    instance: VideoFile,
    *,
    force: bool = False,
) -> None:
    """Return only after local raw and processed HLS are both ready."""
    for artifact_kind in ("raw", "processed"):
        result = materialize_video_hls(
            int(instance.pk),
            artifact_kind=artifact_kind,
            force=force,
        )
        logger.info(
            "%s HLS is ready: video=%s status=%s",
            artifact_kind.capitalize(),
            instance.pk,
            result.status,
        )


def ensure_processed_video_hls(
    instance: VideoFile,
    *,
    force: bool = False,
) -> None:
    """Compatibility wrapper for callers predating required raw HLS."""
    ensure_video_hls(instance, force=force)


def _ensure_instance_state(
    instance: VideoFile | RawPdfFile,
) -> RawPdfState | VideoState | None:
    """
    Helper: ensure instance.state exists and return it.
    Mirrors PdfImportService._ensure_state.
    """
    stateful_instance = cast(_StatefulImportInstance, instance)
    state = stateful_instance.state

    if state is not None:
        return state

    state = stateful_instance.get_or_create_state()
    stateful_instance.save()
    return state


def mark_instance_processing_started(
    instance: RawPdfFile | VideoFile,
    ctx: ImportContext,
) -> None:
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:
            processable_state = cast(_ProcessableState, state)
            # In the old code, processing_started was set earlier; we guard here
            if not processable_state.processing_started:
                processable_state.mark_processing_started()


def finalize_report_success(
    ctx: ImportContext,
) -> None:
    """
    Finalize a successful instance import/anonymization.

    - Move anonymized Report from temp to canonical anonymized dir
    - Update RawPdfFile.processed_file and .anonymized flag
    - Mark RawPdfState as anonymized + sensitive_meta_processed
    - Mark ProcessingHistory.success = True
    """
    instance = ctx.current_report
    if not isinstance(instance, RawPdfFile):
        raise RuntimeError(
            "Cannot finalize report import without a RawPdfFile instance."
        )
    if not instance.pk:
        raise RuntimeError("Cannot finalize report import with an unsaved RawPdfFile.")

    # --- Move anonymized path into final storage ---
    if ctx.anonymized_path is None:
        raise RuntimeError(
            "Cannot finalize report import without an anonymized PDF output "
            f"(instance={instance.pk}, hash={getattr(instance, 'pdf_hash', None)})."
        )

    pdf_hash = getattr(instance, "pdf_hash", None) or instance.pk
    expected_final_path = _processed_report_dir() / f"{pdf_hash}.pdf"
    src = Path(ctx.anonymized_path)

    logger.debug(
        "finalize_report_success: src=%s (exists=%s, resolved=%s), expected_final=%s",
        src,
        src.exists(),
        src.resolve(),
        expected_final_path,
    )

    if not src.exists() or not src.is_file() or src.stat().st_size <= 0:
        raise RuntimeError(
            f"Cannot finalize report import because anonymized output is missing or empty: {src}"
        )

    if requires_app_encrypted_storage(PayloadKind.REPORT_PDF):
        relative_name = path_utils.to_storage_relative(expected_final_path)
        saved_name = _store_existing_final_file(
            instance.processed_file,
            src,
            relative_name=relative_name,
        )
        logger.info("Updated processed_file to %s", saved_name)
        if src.resolve() != expected_final_path.resolve():
            safe_cleanup_staging_file(
                src,
                label="processed report staging output",
                missing_ok=True,
            )
    else:
        if src.resolve() == expected_final_path.resolve():
            logger.info(
                "Anonymizer output already at final path %s; skipping move.",
                expected_final_path,
            )
            final_path = expected_final_path
        else:
            if expected_final_path.exists():
                safe_unlink_file(expected_final_path, missing_ok=True)
            atomic_move_file(source=src, destination=expected_final_path)
            final_path = expected_final_path
            logger.info("Moved anonymized report to %s", final_path)

        relative_name = path_utils.to_storage_relative(final_path)
        current_name = getattr(instance.processed_file, "name", None)
        if current_name != relative_name:
            instance.processed_file.name = relative_name
            logger.info("Updated processed_file to %s", relative_name)

    cast(_StatefulImportInstance, instance).save()
    processed_file_sha256 = verify_and_persist_processed_report_sha256(instance)
    logger.info(
        "Verified processed report artifact: report=%s sha256=%s",
        instance.pk,
        processed_file_sha256,
    )

    # --- Update RawPdfState flags (mirrors _finalize_processing) ---
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:
            processable_state = cast(_ProcessableState, state)
            if not processable_state.processing_started:
                processable_state.mark_processing_started()

            # We consider text/meta extraction + anonymization done at this point
            processable_state.mark_anonymized()
            processable_state.mark_sensitive_meta_processed()

            processable_state.save()

        cast(_StatefulImportInstance, instance).save()

    if not isinstance(ctx.file_hash, str):
        ctx.file_hash = sha256_file(ctx.file_path)
    with transaction.atomic():
        ProcessingHistory.get_or_create_for_hash(
            obj=instance,
            file_hash=ctx.file_hash,
            success=True,
        )

    if isinstance(ctx.sensitive_path, Path):
        safe_cleanup_staging_file(
            ctx.sensitive_path,
            label="report sensitive staging copy after success",
            missing_ok=False,
        )


def finalize_video_success(
    ctx: ImportContext,
) -> None:
    """
    Finalize a successful video import/anonymization.

    - Move anonymized video from temp to canonical anonymized dir
    - Update VideoFile.processed_file
    - Update VideoFile.processed_video_hash
    - Mark VideoState as anonymized + sensitive_meta_processed
    - Mark ProcessingHistory.success = True
    """

    instance = ctx.current_video
    if not isinstance(instance, VideoFile):
        logger.warning("finalize_video_success called with non-VideoFile instance")
        return
    if not instance.pk:
        logger.warning("finalize_video_success called with unsaved instance")
        return

    # --- Move anonymized path into final storage ---
    if ctx.anonymized_path is None:
        raise RuntimeError(
            "Cannot finalize video import without anonymized output "
            f"(instance={instance.pk}, hash={getattr(instance, 'video_hash', None)})."
        )
    else:
        # Use a stable naming convention: <video_hash>.mp4
        video_hash = getattr(instance, "video_hash", None) or instance.pk
        expected_final_path = _processed_video_dir() / f"{video_hash}.mp4"

        src = Path(ctx.anonymized_path)

        logger.debug(
            "finalize_video_success: src=%s (exists=%s, resolved=%s), expected_final=%s",
            src,
            src.exists(),
            src.resolve(),
            expected_final_path,
        )

        # If anonymizer already wrote to the final path, don't move
        try:
            same_target = src.resolve() == expected_final_path.resolve()
        except FileNotFoundError:
            # src might not exist anymore
            same_target = False

        if not src.exists():
            logger.error(
                "Anonymized video %s does not exist; cannot finalize to %s",
                src,
                expected_final_path,
            )
            raise RuntimeError(
                f"Cannot finalize video import because anonymized output is missing: {src}"
            )
        else:
            _verify_final_video_output(src)
            if ctx.storage_normalization_evidence is None:
                raise RuntimeError(
                    "Cannot finalize video without storage-normalization evidence."
                )
            instance.processed_video_hash = sha256_file(src)
            relative_name = path_utils.to_storage_relative(expected_final_path)
            _require_execution_ownership(ctx)
            saved_name = _store_existing_final_file(
                instance.processed_file,
                src,
                relative_name=relative_name,
            )
            logger.info("Updated video processed_file to %s", saved_name)
            if not same_target:
                safe_cleanup_staging_file(
                    src,
                    label="processed video staging output",
                    missing_ok=True,
                )

    _require_execution_ownership(ctx)
    existing_meta = dict(instance.meta or {})
    existing_meta["storage_normalization"] = evidence_as_json(
        ctx.storage_normalization_evidence
    )
    instance.meta = existing_meta
    cast(_StatefulImportInstance, instance).save()
    # HLS readiness is part of import success. A failed transcode must leave the
    # import retryable instead of publishing a successful but unstreamable video.
    _require_execution_ownership(ctx)
    ensure_video_hls(instance, force=True)
    _require_execution_ownership(ctx)

    # --- Update VideoState flags (mirrors report) ---
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        _require_execution_ownership(ctx)
        _record_successful_video_processing_history(ctx)
        if state is not None:
            processable_state = cast(_ProcessableState, state)
            if not processable_state.processing_started:
                processable_state.mark_processing_started()

            processable_state.mark_anonymized()
            processable_state.mark_sensitive_meta_processed()

            processable_state.save()

        cast(_StatefulImportInstance, instance).save()

    _require_execution_ownership(ctx)
    if isinstance(ctx.sensitive_path, Path):
        safe_cleanup_staging_file(
            ctx.sensitive_path,
            label="video sensitive staging copy after success",
            missing_ok=False,
        )


def finalize_failure(
    ctx: ImportContext,
    *,
    preserve_existing_video_artifacts: bool = False,
    preserve_sensitive_staging: bool = False,
) -> None:
    """
    Finalize a failed instance import/anonymization.

    - Reset RawPdfState flags to "not processed"
    - Mark ProcessingHistory.success = False
    - Delete all associated files, unless an in-place video re-import failed
      before committing its staged replacement
    - Preserve the current sensitive staging snapshot only when a fenced retry
      reset explicitly requests it
    """

    if ctx.instance is None:
        if isinstance(ctx.current_report, RawPdfFile):
            ctx.instance = ctx.current_report
        elif isinstance(ctx.current_video, VideoFile):
            ctx.instance = ctx.current_video
        else:
            raise Exception

    # History entry with success=False
    if not isinstance(ctx.file_hash, str):
        ctx.file_hash = sha256_file(ctx.file_path)
    ProcessingHistory.get_or_create_for_hash(
        file_hash=ctx.file_hash,
        success=False,
    )

    # Reset state flags similar to _mark_processing_incomplete / _cleanup_on_error
    state = _ensure_instance_state(ctx.instance)

    if state is not None:
        try:
            processable_state = cast(_ProcessableState, state)
            if isinstance(ctx.instance, RawPdfFile):
                processable_state.mark_processing_failed()
            else:
                processable_state.mark_processing_not_started()

            processable_state.save()
            logger.info(
                "Reset instance state for failed processing (instance pk=%s)",
                ctx.instance.pk,
            )
        except Exception as e:
            logger.warning(
                "Failed to reset State for instance %s: %s",
                ctx.instance.pk,
                e,
            )

    try:
        delete_associated_files(
            ctx,
            preserve_existing_video_artifacts=preserve_existing_video_artifacts,
            preserve_sensitive_staging=preserve_sensitive_staging,
        )
    except Exception as e:
        logger.warning(f"There might be files remaining. {e}")

    logger.error(
        "File processing failed for %s - state reset, ready for retry.",
        ctx.file_path,
    )


def delete_associated_files(
    ctx: ImportContext,
    *,
    preserve_existing_video_artifacts: bool = False,
    preserve_sensitive_staging: bool = False,
) -> None:
    """
    Best-effort cleanup of anonymized, sensitive and transcoding artefacts.

    - Ensure ctx.original_path points to an existing import file; if not, try to restore
      from ctx.sensitive_path into the appropriate IMPORT_*_DIR.
    - Delete anonymized file (if any).
    - Delete known transient paths recorded on the import context.
    - Delete sensitive file (if any), unless it is the explicitly preserved
      input snapshot for the current retry attempt.

    This function should *not* raise on non-critical cleanup errors; it logs instead.
    Only restoration of the original import file is treated as critical.
    """

    if not preserve_existing_video_artifacts:
        _delete_video_streamable_artifacts(ctx)

    # --- Delete anonymized file (best-effort) ---
    if isinstance(ctx.anonymized_path, Path):
        try:
            safe_cleanup_staging_file(
                ctx.anonymized_path,
                label="failed anonymized staging output",
                missing_ok=False,
            )
        except Exception as e:
            logger.error(
                "Error when unlinking anonymized path %s: %s",
                ctx.anonymized_path,
                e,
                exc_info=True,
            )
        finally:
            ctx.anonymized_path = None

    # --- Delete sensitive file (best-effort) ---
    if not preserve_sensitive_staging and isinstance(ctx.sensitive_path, Path):
        try:
            safe_cleanup_staging_file(
                ctx.sensitive_path,
                label="failed sensitive staging copy",
                missing_ok=False,
            )
        except Exception as e:
            logger.error(
                "Error when unlinking sensitive path %s: %s",
                ctx.sensitive_path,
                e,
                exc_info=True,
            )
        finally:
            ctx.sensitive_path = None


def _delete_video_streamable_artifacts(ctx: ImportContext) -> None:
    video = ctx.instance if isinstance(ctx.instance, VideoFile) else ctx.current_video
    if not isinstance(video, VideoFile):
        return

    update_fields: list[str] = []
    for field_name in (
        "raw_streamable_relative_path",
        "processed_streamable_relative_path",
    ):
        relative_path = getattr(video, field_name, "")
        if not relative_path:
            continue

        artifact_path = path_utils.resolve_existing_protected_media_path(relative_path)
        if artifact_path is not None:
            try:
                safe_unlink_file(artifact_path, missing_ok=False)
                logger.info("Deleted streamable video artifact %s", artifact_path)
            except Exception as exc:
                logger.error(
                    "Error when unlinking streamable video artifact %s: %s",
                    artifact_path,
                    exc,
                    exc_info=True,
                )

        setattr(video, field_name, "")
        update_fields.append(field_name)

    if update_fields and video.pk:
        cast(_StatefulImportInstance, video).save(update_fields=update_fields)


def nuke_transcoding_dir(transcoding_dir: str | Path | None = None) -> bool:
    """
    Delete all files and subdirectories inside the transcoding directory.

    Returns:
        True if the directory was either empty / successfully cleaned,
        False if something went wrong (error is logged).
    """
    try:
        if transcoding_dir is None:
            transcoding_dir = path_utils.data_paths["transcoding"]

        transcoding_dir = Path(transcoding_dir)

        if not transcoding_dir.exists():
            logger.info(
                "Transcoding dir %s does not exist; nothing to clean.", transcoding_dir
            )
            return True

        if not transcoding_dir.is_dir():
            logger.error(
                "Configured transcoding path %s is not a directory.", transcoding_dir
            )
            return False

        for entry in transcoding_dir.iterdir():
            try:
                if entry.is_file() or entry.is_symlink():
                    safe_unlink_file(entry, missing_ok=False)
                elif entry.is_dir():
                    staged_entry = entry.with_name(
                        f"{entry.name}.cleanup.{os.getpid()}"
                    )
                    atomic_move_path(source=entry, destination=staged_entry)
                    safe_rmtree(staged_entry, missing_ok=False)
            except Exception as e:
                logger.warning(
                    "Failed to remove entry %s in transcoding dir: %s", entry, e
                )
                # Continue trying to delete other entries
        return True

    except Exception as e:
        logger.error(
            "Unexpected error while nuking transcoding dir: %s", e, exc_info=True
        )
        return False
