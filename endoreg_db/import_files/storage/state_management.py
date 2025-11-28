from django.contrib.contenttypes.models import ContentType
from endoreg_db.models.media.storage.processing_history import ProcessingHistory


def finalize_processing()




def _record_history(self, instance, state, message: str = "") -> None:
    ProcessingHistory.objects.create(
        content_type=ContentType.objects.get_for_model(instance.__class__),
        object_id=instance.pk,
        file_name=getattr(instance, "file").name if hasattr(instance, "file") else "",
        state=state.anonymization_status,
        message=message,
    )


# endoreg_db/import_files/storage/finalize_processing.py

import logging
import shutil
from pathlib import Path
from typing import Optional, Union

from django.db import transaction

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import RawPdfFile, VideoFile
from endoreg_db.models.state import RawPdfState, VideoState
from endoreg_db.models.media.storage import ProcessingHistory
from endoreg_db.utils import paths as path_utils

logger = logging.getLogger(__name__)


def _ensure_instance_state(instance: Union[VideoFile, RawPdfFile]) -> Optional[Union[RawPdfState, VideoState]]:
    """
    Helper: ensure RawPdfFile.state exists and return it.
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


def finalize_success(
    instance: Union[RawPdfFile, VideoFile],
    ctx: ImportContext,
    anonymized_root: Path,
    anonymized_temp_path: Optional[Path],
) -> None:
    """
    Finalize a successful instance import/anonymization.

    - Move anonymized PDF from temp to canonical anonymized dir
    - Update RawPdfFile.processed_file and .anonymized flag
    - Mark RawPdfState as anonymized + sensitive_meta_processed
    - Mark ProcessingHistory.success = True
    """
    if not instance.pk:
        logger.warning("finalize_report_success called with unsaved RawPdfFile")
        return

    # --- Move anonymized PDF into final storage (if we have one) ---
    final_path: Optional[Path] = None

    if anonymized_temp_path is None:
        logger.warning(
            "No anonymized_temp_path for instance %s (hash=%s); "
            "skipping anonymized file move.",
            instance.pk,
            getattr(instance, "pdf_hash", None),
        )
    else:
        anonymized_root.mkdir(parents=True, exist_ok=True)

        # Use same naming convention as old PdfImportService: <hash>_anonymized.pdf
        pdf_hash = getattr(instance, "pdf_hash", None) or instance.pk
        final_path = anonymized_root / f"{pdf_hash}_anonymized.pdf"

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

        shutil.move(str(anonymized_temp_path), str(final_path))
        logger.info(
            "Moved anonymized instance to canonical path: %s",
            final_path,
        )

        # Update FileField to be relative to STORAGE_DIR (same as _apply_anonymized_pdf)
        try:
            relative_name = str(final_path.relative_to(path_utils.STORAGE_DIR))
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
    if ctx.file_hash:
        ProcessingHistory.get_or_create_history(
            object_id=instance.pk,
            file_hash=ctx.file_hash,
            success=True,
        )
    else:
        logger.debug(
            "No file_hash in context for instance %s when finalizing success; "
            "skipping ProcessingHistory.",
            instance.pk,
        )


def finalize_report_failure(
    instance: RawPdfFile,
    ctx: ImportContext,
    error_reason: Optional[str] = None,
) -> None:
    """
    Finalize a failed instance import/anonymization.

    - Reset RawPdfState flags to "not processed"
    - Mark ProcessingHistory.success = False
    - Store error_reason on ImportContext for later quarantine/cleanup
    """
    if error_reason:
        ctx.error_reason = error_reason

    # Reset state flags similar to _mark_processing_incomplete / _cleanup_on_error
    state = _ensure_raw_pdf_state(instance)

    if state is not None:
        try:
            # These fields existed in the previous RawPdfState
            if hasattr(state, "text_meta_extracted"):
                state.text_meta_extracted = False
            if hasattr(state, "pdf_meta_extracted"):
                state.pdf_meta_extracted = False
            if hasattr(state, "sensitive_meta_processed"):
                state.sensitive_meta_processed = False
            if hasattr(state, "anonymized"):
                state.anonymized = False

            state.save()
            logger.info(
                "Reset instance state for failed processing (instance pk=%s, hash=%s)",
                instance.pk,
                getattr(instance, "pdf_hash", None),
            )
        except Exception as e:
            logger.warning(
                "Failed to reset RawPdfState for instance %s: %s",
                instance.pk,
                e,
            )

    # History entry with success=False
    if ctx.file_hash:
        ProcessingHistory.get_or_create_history(
            object_id=instance.pk,
            file_hash=ctx.file_hash,
            success=False,
        )
    else:
        logger.debug(
            "No file_hash in context for instance %s when finalizing failure; "
            "skipping ProcessingHistory.",
            instance.pk,
        )

    logger.error(
        "Report processing failed for %s (hash=%s): %s",
        ctx.file_path,
        ctx.file_hash,
        error_reason or "no reason provided",
    )
