from endoreg_db.models.media.processing_history.processing_history import ProcessingHistory
from endoreg_db.utils.paths import IMPORT_REPORT_DIR, IMPORT_VIDEO_DIR, ANONYM_REPORT_DIR, ANONYM_VIDEO_DIR

import logging
import shutil
from pathlib import Path
from typing import Optional, Union

from django.db import transaction

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state import RawPdfState, VideoState
from endoreg_db.utils import paths as path_utils

logger = logging.getLogger(__name__)


def _ensure_instance_state(instance: Union[VideoFile, RawPdfFile]) -> Optional[Union[RawPdfState, VideoState]]:
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
    ctx: ImportContext,):
    
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
            ProcessingHistory.get_or_create_for_object(
                obj=instance,
                success=True,
            )
    except Exception as e:
        logger.debug(
            "Saving not possible; %s"
            f"skipping ProcessingHistory.{e}",
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
        logger.warning("Transcoding directory cleanup returned False after finalize_video_success; there may be leftover files.")

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
            ProcessingHistory.get_or_create_for_object(
                obj=instance,
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
    """
    if ctx.instance is None:
        if isinstance(ctx.current_report, RawPdfFile):
            ctx.instance = ctx.current_report
        elif isinstance(ctx.current_video, VideoFile):
            ctx.instance = ctx.current_video
        else:
            raise Exception
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

    # History entry with success=False
    if ctx.file_hash:
        ProcessingHistory.get_or_create_for_object(
            obj=ctx.instance,
            success=False,
        )
    else:
        logger.debug(
            "No file_hash in context for instance %s when finalizing failure; "
            "skipping ProcessingHistory.",
            ctx.instance.pk,
        )

    logger.error(
        "Report processing failed for %s",
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

    # --- 1. Restore original import file if needed (critical) ---
    original_path = getattr(ctx, "original_path", None)
    original_missing = not isinstance(original_path, Path) or not original_path.exists()
    if original_missing:
        logger.warning(
            "Original file missing in ctx (file_type=%s); "
            "trying to restore from sensitive copy.",
            ctx.file_type,
        )

        if not isinstance(ctx.sensitive_path, Path) or not ctx.sensitive_path.exists():
            # This is serious: we lost both original and sensitive copy
            msg = (
                f"Cannot restore original file for {ctx.file_type}: "
                "sensitive copy missing as well."
            )
            logger.error(msg)
            raise RuntimeError(msg)

        try:
            if ctx.file_type == "video":
                target_dir = IMPORT_VIDEO_DIR
            elif ctx.file_type == "report":
                target_dir = IMPORT_REPORT_DIR
            else:
                raise ValueError(f"Unknown file_type in context: {ctx.file_type}")

            target_dir.mkdir(parents=True, exist_ok=True)
            restored_path = shutil.copy2(ctx.sensitive_path, target_dir)
            ctx.original_path = Path(restored_path)
            logger.info("Restored original file for %s to %s", ctx.file_type, ctx.original_path)
        except Exception as e:
            logger.error("Error during safety copy / restore of original file: %s", e, exc_info=True)
            raise

    # --- 2. Delete anonymized file (best-effort) ---
    if isinstance(ctx.anonymized_path, Path):
        try:
            if ctx.anonymized_path.exists():
                ctx.anonymized_path.unlink()
                logger.info("Deleted anonymized file %s", ctx.anonymized_path)
        except Exception as e:
            logger.error("Error when unlinking anonymized path %s: %s", ctx.anonymized_path, e, exc_info=True)
        finally:
            ctx.anonymized_path = None

    # --- 3. Nuke transcoding directory (best-effort) ---
    if not nuke_transcoding_dir():
        logger.warning("Transcoding directory cleanup returned False; there may be leftover files.")

    # --- 4. Delete sensitive file (best-effort) ---
    if isinstance(ctx.sensitive_path, Path):
        try:
            if ctx.sensitive_path.exists():
                ctx.sensitive_path.unlink()
                logger.info("Deleted sensitive file %s", ctx.sensitive_path)
        except Exception as e:
            logger.error("Error when unlinking sensitive path %s: %s", ctx.sensitive_path, e, exc_info=True)
        finally:
            ctx.sensitive_path = None

    
def nuke_transcoding_dir(
    transcoding_dir: Union[str, Path, None] = None
) -> bool:
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
            logger.info("Transcoding dir %s does not exist; nothing to clean.", transcoding_dir)
            return True

        if not transcoding_dir.is_dir():
            logger.error("Configured transcoding path %s is not a directory.", transcoding_dir)
            return False

        for entry in transcoding_dir.iterdir():
            try:
                if entry.is_file() or entry.is_symlink():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
            except Exception as e:
                logger.warning("Failed to remove entry %s in transcoding dir: %s", entry, e)
                # Continue trying to delete other entries
        return True

    except Exception as e:
        logger.error("Unexpected error while nuking transcoding dir: %s", e, exc_info=True)
        return False
