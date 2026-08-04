import logging
import os
from pathlib import Path
from typing import Optional, Union

from django.db import transaction

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.cleanup import safe_cleanup_staging_file
from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state import RawPdfState, VideoState
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.streamable_media import sync_video_streamable_artifacts
from endoreg_db.utils.filesystem import paths as path_utils
from endoreg_db.utils.file_operations import (
    atomic_move_file,
    atomic_move_path,
    safe_rmtree,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.storage import save_local_file
from endoreg_db.utils.storage.profile import PayloadKind, requires_app_encrypted_storage
from endoreg_db.utils.video.ffmpeg_wrapper import get_stream_info

logger = logging.getLogger(__name__)


def _processed_report_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().anonym_report


def _processed_video_dir() -> Path:
    return path_utils.EndoregPathsModel.from_environment().anonym_video


def _verify_final_video_output(path: Path) -> None:
    """Fail finalization if the committed anonymized video is not probeable."""
    if not path.exists():
        raise RuntimeError(f"Final anonymized video missing: {path}")
    stream_info = get_stream_info(path)
    if not stream_info or "streams" not in stream_info:
        raise RuntimeError(f"Final anonymized video failed ffprobe validation: {path}")
    has_video_stream = any(
        stream.get("codec_type") == "video" for stream in stream_info["streams"]
    )
    if not has_video_stream:
        raise RuntimeError(f"Final anonymized video has no video stream: {path}")


def _store_existing_final_file(
    field_file,
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
    if (
        callable(repair_plaintext_file)
        and storage_path is not None
        and final_path.resolve() == storage_path
    ):
        repair_plaintext_file(relative_name)
        return relative_name
    return save_local_file(
        field_file,
        final_path,
        name=relative_name,
        save=False,
        overwrite=True,
    )


def _get_history_filename(ctx: ImportContext) -> str:
    """
    Prefer original_path.name if provided, otherwise fall back to file_path.name.
    """
    if ctx.original_path is not None:
        return ctx.original_path.name
    # ctx.file_path is always present and already a Path in your tests
    return Path(ctx.file_path).name


def _ensure_instance_state(
    instance: Union[VideoFile, RawPdfFile],
) -> Optional[Union[RawPdfState, VideoState]]:
    """
    Helper: ensure instance.state exists and return it.
    Mirrors PdfImportService._ensure_state.
    """
    if isinstance(instance, RawPdfFile):
        state = getattr(instance, "state", None)
    else:
        state = getattr(instance, "state", None)

    if state is not None:
        return state

    if hasattr(instance, "get_or_create_state"):
        state = instance.get_or_create_state()
        instance.save()
        return state

    return None


def mark_instance_processing_started(
    instance: Union[RawPdfFile, VideoFile],
    ctx: ImportContext,
):
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:
            # In the old code, processing_started was set earlier; we guard here
            if not getattr(state, "processing_started", False) and hasattr(
                state, "mark_processing_started"
            ):
                state.mark_processing_started()


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
        logger.warning("finalize_success called with unsaved instance")
        return
    if not instance.pk:
        logger.warning("finalize_success called with unsaved instance")
        return

    # --- Move anonymized path into final storage (if we have one) ---
    final_path: Optional[Path] = None
    if ctx.anonymized_path is None:
        logger.warning(
            "No anonymized_path for instance %s (hash=%s); skipping file move.",
            instance.pk,
            getattr(instance, "pdf_hash", None),
        )
        final_path = None
    else:
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

        if not src.exists():
            logger.error(
                "Anonymized file %s does not exist; cannot finalize to %s",
                src,
                expected_final_path,
            )
            final_path = None
        elif requires_app_encrypted_storage(PayloadKind.REPORT_PDF):
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

            if final_path is not None:
                relative_name = path_utils.to_storage_relative(final_path)
                current_name = getattr(instance.processed_file, "name", None)
                if current_name != relative_name:
                    instance.processed_file.name = relative_name
                    logger.info("Updated processed_file to %s", relative_name)

    # --- Update RawPdfState flags (mirrors _finalize_processing) ---
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:
            if not getattr(state, "processing_started", False) and hasattr(
                state, "mark_processing_started"
            ):
                state.mark_processing_started()

            # We consider text/meta extraction + anonymization done at this point
            if hasattr(state, "mark_anonymized"):
                state.mark_anonymized()
            if hasattr(state, "mark_sensitive_meta_processed"):
                state.mark_sensitive_meta_processed()

            state.save()

        instance.save()

    if isinstance(ctx.sensitive_path, Path):
        safe_cleanup_staging_file(
            ctx.sensitive_path,
            label="report sensitive staging copy after success",
            missing_ok=False,
        )

    # --- ProcessingHistory entry ---
    try:
        with transaction.atomic():
            if not isinstance(ctx.file_hash, str):
                ctx.file_hash = sha256_file(ctx.file_path)
            ProcessingHistory.get_or_create_for_hash(
                obj=instance,
                file_hash=ctx.file_hash,
                success=True,
            )
    except Exception as e:
        logger.debug(
            f"Saving not possible; %sskipping ProcessingHistory.{e}",
            instance.pk,
        )


def finalize_video_success(
    ctx: ImportContext,
) -> None:
    """
    Finalize a successful video import/anonymization.

    - Move anonymized video from temp to canonical anonymized dir
    - Update VideoFile.processed_file
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
    final_path: Optional[Path] = None

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
        elif requires_app_encrypted_storage(PayloadKind.VIDEO_PROCESSED):
            _verify_final_video_output(src)
            relative_name = path_utils.to_storage_relative(expected_final_path)
            saved_name = _store_existing_final_file(
                instance.processed_file,
                src,
                relative_name=relative_name,
            )
            logger.info("Updated video processed_file to %s", saved_name)
            if src.resolve() != expected_final_path.resolve():
                safe_cleanup_staging_file(
                    src,
                    label="processed video staging output",
                    missing_ok=True,
                )
        else:
            if same_target:
                logger.info(
                    "Anonymizer output already at final video path %s; skipping move.",
                    expected_final_path,
                )
                final_path = expected_final_path
            else:
                if expected_final_path.exists():
                    try:
                        safe_unlink_file(expected_final_path, missing_ok=True)
                    except Exception as e:
                        logger.warning(
                            "Could not remove existing anonymized video %s: %s",
                            expected_final_path,
                            e,
                        )
                atomic_move_file(source=src, destination=expected_final_path)
                final_path = expected_final_path
                logger.info("Moved anonymized video to %s", final_path)

            if final_path is not None:
                _verify_final_video_output(final_path)
                relative_name = path_utils.to_storage_relative(final_path)
                current_name = getattr(instance.processed_file, "name", None)
                if current_name != relative_name:
                    instance.processed_file.name = relative_name
                    logger.info("Updated video processed_file to %s", relative_name)

    # --- Update VideoState flags (mirrors report) ---
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:
            if not getattr(state, "processing_started", False) and hasattr(
                state, "mark_processing_started"
            ):
                state.mark_processing_started()

            if hasattr(state, "mark_anonymized"):
                state.mark_anonymized()
            if hasattr(state, "mark_sensitive_meta_processed"):
                state.mark_sensitive_meta_processed()

            state.save()

        instance.save()

    try:
        sync_video_streamable_artifacts(
            instance,
            include_raw=True,
            include_processed=True,
            save=True,
        )
    except Exception as exc:
        logger.warning(
            "Could not synchronize streamable artifacts for video %s after finalize: %s",
            instance.pk,
            exc,
        )

    if isinstance(ctx.sensitive_path, Path):
        safe_cleanup_staging_file(
            ctx.sensitive_path,
            label="video sensitive staging copy after success",
            missing_ok=False,
        )

    # --- ProcessingHistory entry ---
    try:
        with transaction.atomic():
            if not isinstance(ctx.file_hash, str):
                ctx.file_hash = sha256_file(ctx.file_path)
            ProcessingHistory.get_or_create_for_hash(
                file_hash=ctx.file_hash,
                success=True,
            )
    except Exception as e:
        logger.debug(
            "Saving not possible for video %s; skipping ProcessingHistory. Error: %s",
            instance.pk,
            e,
        )


def finalize_failure(
    ctx: ImportContext,
) -> None:
    """
    Finalize a failed instance import/anonymization.

    - Reset RawPdfState flags to "not processed"
    - Mark ProcessingHistory.success = False
    - Delete all associated files
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
            state.mark_processing_not_started()

            state.save()
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
        delete_associated_files(ctx)
    except Exception as e:
        logger.warning(f"There might be files remaining. {e}")

    logger.error(
        "File processing failed for %s - state reset, ready for retry.",
        ctx.file_path,
    )


def delete_associated_files(ctx: ImportContext) -> None:
    """
    Best-effort cleanup of anonymized, sensitive and transcoding artefacts.

    - Ensure ctx.original_path points to an existing import file; if not, try to restore
      from ctx.sensitive_path into the appropriate IMPORT_*_DIR.
    - Delete anonymized file (if any).
    - Delete known transient paths recorded on the import context.
    - Delete sensitive file (if any).

    This function should *not* raise on non-critical cleanup errors; it logs instead.
    Only restoration of the original import file is treated as critical.
    """

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
    if isinstance(ctx.sensitive_path, Path):
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
        video.save(update_fields=update_fields)


def nuke_transcoding_dir(transcoding_dir: Union[str, Path, None] = None) -> bool:
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
