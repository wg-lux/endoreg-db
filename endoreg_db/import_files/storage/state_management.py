from endoreg_db.models.media.storage.processing_history import ProcessingHistory
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
    if not isinstance(instance, Union[VideoFile, RawPdfFile]):
        logger.warning("finalize_success called with unsaved instance")
        return
    if not instance.pk:
        logger.warning("finalize_success called with unsaved instance")
        return

    # --- Move anonymized path into final storage (if we have one) ---
    final_path: Optional[Path] = None

    if ctx.anonymized_path is None:
        logger.warning(
            "No anonymized_temp_path for instance %s (hash=%s); "
            "skipping anonymized file move.",
            instance.pk,
            getattr(instance, "pdf_hash", None),
        )
    else:
        ANONYM_REPORT_DIR.mkdir(parents=True, exist_ok=True)

        # Use same naming convention as old PdfImportService: <hash>_anonymized.pdf
        pdf_hash = getattr(instance, "pdf_hash", None) or instance.pk
        final_path = ANONYM_REPORT_DIR / f"{pdf_hash}.pdf"

        # Replace any existing file for this hash
        if final_path.exists():
            try:
                final_path.unlink()
            except Exception as e:
                logger.warning(
                    "Could not remove existing anonymized instance %s: %s",
                    final_path,
                    e,
                )

        shutil.move(str(ctx.anonymized_path), str(final_path))
        logger.info(
            "Moved anonymized instance to canonical path: %s",
            final_path,
        )

        try:
            relative_name = str(ctx.anonymized_path)
        except ValueError:
            # Fallback: absolute path if outside STORAGE_DIR
            relative_name = str(final_path)

        current_name = getattr(instance.processed_file, "name", None)
        if current_name != relative_name:
            instance.processed_file.name = relative_name
            logger.info(
                "Updated processed_file reference to: %s",
                instance.processed_file.name,
            )


    # --- Update RawPdfState flags (mirrors _finalize_processing) ---
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:

            # In the old code, processing_started was set earlier; we guard here
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
    Finalize a successful instance import/anonymization.

    - Move anonymized Report from temp to canonical anonymized dir
    - Update RawPdfFile.processed_file and .anonymized flag
    - Mark RawPdfState as anonymized + sensitive_meta_processed
    - Mark ProcessingHistory.success = True
    """
    instance = ctx.current_video
    if not isinstance(instance, VideoFile):
        logger.warning("finalize_success called with unsaved instance")
        return
    if not instance.pk:
        logger.warning("finalize_success called with unsaved instance")
        return

    # --- Move anonymized path into final storage (if we have one) ---
    final_path: Optional[Path] = None

    if ctx.anonymized_path is None:
        logger.warning(
            "No anonymized_temp_path for instance %s (hash=%s); "
            "skipping anonymized file move.",
            instance.pk,
            getattr(instance, "video_hash", None),
        )

    else:

        # Use same naming convention as old PdfImportService: <hash>_anonymized.pdf
        video_hash = getattr(instance, "video_hash", None),

        final_path = ANONYM_VIDEO_DIR / f"{video_hash}.mp4"

        logger.info(
            "Moved anonymized instance to canonical path: %s",
            final_path,
        )
        


        # Update FileField to be relative to STORAGE_DIR (same as _apply_anonymized_pdf)
        try:
            relative_name = str(ctx.anonymized_path)
        except ValueError:
            # Fallback: absolute path if outside STORAGE_DIR
            relative_name = str(final_path)

        current_name = getattr(instance.processed_file, "name", None)
        if current_name != relative_name:
            instance.processed_file.name = relative_name
            logger.info(
                "Updated processed_file reference to: %s",
                instance.processed_file.name,
            )


    # --- Update RawPdfState flags (mirrors _finalize_processing) ---
    state = _ensure_instance_state(instance)

    with transaction.atomic():
        if state is not None:

            # In the old code, processing_started was set earlier; we guard here
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
            f"Saving not possible: {e} skipping ProcessingHistory.",
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

def delete_associated_files(ctx:ImportContext):
    try:
        assert isinstance(ctx.original_path, Path)
    except AssertionError as e:
        logger.warning(f"Original file restored from sensitive copy. This is because the original file is gone. {e}")
        if ctx.file_type =="video":
            if isinstance(ctx.sensitive_path, Path):
                try:
                    ctx.original_path = Path(shutil.copy2(ctx.sensitive_path, IMPORT_VIDEO_DIR))
                except Exception as e:
                    logger.error(f"Error during safety copy: {e} Original file could not be restored!")
                    raise
        elif ctx.file_type == "report":
            if isinstance(ctx.sensitive_path, Path):
                try:
                    ctx.original_path = Path(shutil.copy2(ctx.sensitive_path, IMPORT_REPORT_DIR))
                except Exception as e:
                    logger.error(f"Error during safety copy: {e} Original file could not be restored!")
                    raise
        
    if isinstance(ctx.anonymized_path, Path):
        try:
            Path.unlink(ctx.anonymized_path)
            assert(ctx.anonymized_path is not Path)

        except Exception as e:
            logger.error(f"Error when unlinking anonymized path. {e}")
            raise
        
    ctx.anonymized_path = None
    
    if isinstance(ctx.sensitive_path, Path):
        try:
            Path.unlink(ctx.sensitive_path)
            assert(ctx.sensitive_path is not Path)
            
        except Exception as e:
            logger.error(f"Error when unlinking anonymized path. {e}")

            raise
        
    ctx.sensitive_path = None
    