from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.utils.paths import ANONYM_REPORT_DIR, ANONYM_VIDEO_DIR

import logging
import shutil
from pathlib import Path
from typing import Optional, Union

from django.db import transaction

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state import RawPdfState, VideoState
from endoreg_db.utils import paths as path_utils
from endoreg_db.utils.file_operations import sha256_file

logger = logging.getLogger(__name__)


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
        expected_final_path = ANONYM_REPORT_DIR / f"{pdf_hash}.pdf"

        src = Path(ctx.anonymized_path)

        logger.debug(
            "finalize_report_success: src=%s (exists=%s, resolved=%s), expected_final=%s",
            src,
            src.exists(),
            src.resolve(),
            expected_final_path,
        )

        # If anonymizer already wrote to the final path, don't move
        if src.resolve() == expected_final_path.resolve():
            logger.info(
                "Anonymizer output already at final path %s; skipping move.",
                expected_final_path,
            )
            final_path = expected_final_path
        else:
            # Only move if the source actually exists
            if not src.exists():
                logger.error(
                    "Anonymized file %s does not exist; cannot move to %s",
                    src,
                    expected_final_path,
                )
                final_path = None
            else:
                ANONYM_REPORT_DIR.mkdir(parents=True, exist_ok=True)
                if expected_final_path.exists():
                    expected_final_path.unlink()
                shutil.move(str(src), str(expected_final_path))
                final_path = expected_final_path
                logger.info("Moved anonymized report to %s", final_path)

        # Update FileField if we have a final path
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

    # --- Move anonymized path into final storage (if we have one) ---
    final_path: Optional[Path] = None

    if ctx.anonymized_path is None:
        logger.warning(
            "No anonymized_path for video instance %s (hash=%s); skipping file move.",
            instance.pk,
            getattr(instance, "video_hash", None),
        )
    else:
        # Use a stable naming convention: <video_hash>.mp4
        video_hash = getattr(instance, "video_hash", None) or instance.pk
        expected_final_path = ANONYM_VIDEO_DIR / f"{video_hash}.mp4"

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

        if same_target:
            logger.info(
                "Anonymizer output already at final video path %s; skipping move.",
                expected_final_path,
            )
            final_path = expected_final_path
        else:
            if not src.exists():
                logger.error(
                    "Anonymized video %s does not exist; cannot move to %s",
                    src,
                    expected_final_path,
                )
                final_path = None
            else:
                ANONYM_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
                if expected_final_path.exists():
                    try:
                        expected_final_path.unlink()
                    except Exception as e:
                        logger.warning(
                            "Could not remove existing anonymized video %s: %s",
                            expected_final_path,
                            e,
                        )
                shutil.move(str(src), str(expected_final_path))
                final_path = expected_final_path
                logger.info("Moved anonymized video to %s", final_path)

        # Update FileField if we have a final path
        if final_path is not None:
            relative_name = path_utils.to_storage_relative(final_path)
            current_name = getattr(instance.processed_file, "name", None)
            if current_name != relative_name:
                instance.processed_file.name = relative_name
                logger.info("Updated video processed_file to %s", relative_name)

    if not nuke_transcoding_dir():
        logger.warning(
            "Transcoding directory cleanup returned False after finalize_video_success; there may be leftover files."
        )

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
    - Nuke transcoding directory.
    - Delete sensitive file (if any).

    This function should *not* raise on non-critical cleanup errors; it logs instead.
    Only restoration of the original import file is treated as critical.
    """

    # --- Delete anonymized file (best-effort) ---
    if isinstance(ctx.anonymized_path, Path):
        try:
            if ctx.anonymized_path.exists():
                ctx.anonymized_path.unlink()
                logger.info("Deleted anonymized file %s", ctx.anonymized_path)
        except Exception as e:
            logger.error(
                "Error when unlinking anonymized path %s: %s",
                ctx.anonymized_path,
                e,
                exc_info=True,
            )
        finally:
            ctx.anonymized_path = None

    # --- Nuke transcoding directory (best-effort) ---
    if not nuke_transcoding_dir():
        logger.warning(
            "Transcoding directory cleanup returned False; there may be leftover files."
        )

    # --- Delete sensitive file (best-effort) ---
    if isinstance(ctx.sensitive_path, Path):
        try:
            if ctx.sensitive_path.exists():
                ctx.sensitive_path.unlink()
                logger.info("Deleted sensitive file %s", ctx.sensitive_path)
        except Exception as e:
            logger.error(
                "Error when unlinking sensitive path %s: %s",
                ctx.sensitive_path,
                e,
                exc_info=True,
            )
        finally:
            ctx.sensitive_path = None


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
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
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
