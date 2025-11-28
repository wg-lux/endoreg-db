# endoreg_db/services/report_error_cleanup.py
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, MutableSet, Optional

from endoreg_db.utils import paths as path_utils

logger = logging.getLogger(__name__)


def cleanup_report_on_error(
    self,
    *,
    current_pdf: Any,
    original_path: Optional[Path],
    processing_context: Dict[str, Any],
    processed_files: MutableSet[str],
    get_pdf_dir: Callable[[], Optional[Path]],
) -> None:
    """
    Cleanup processing context on error.

    Args:
        current_pdf: RawPdfFile instance or None
        original_path: Original ingress report path, if known
        processing_context: PdfImportService.processing_context dict
        processed_files: PdfImportService.processed_files set
        get_pdf_dir: Callable resolving the report directory (PdfImportService._get_pdf_dir)
    """
    try:
        if current_pdf and hasattr(current_pdf, "state"):
            state = getattr(current_pdf, "state", None)
            if not state and hasattr(current_pdf, "get_or_create_state"):
                state = current_pdf.get_or_create_state()

            # Restore original file from raw file path
            raw_file_path = None
            if hasattr(current_pdf, "get_raw_file_path"):
                raw_file_path = current_pdf.get_raw_file_path()

            if raw_file_path is not None and original_path is not None:
                try:
                    shutil.copy2(str(raw_file_path), str(original_path))
                except Exception as e:
                    logger.warning(
                        "Failed to restore original report from %s to %s: %s",
                        raw_file_path,
                        original_path,
                        e,
                    )

            # Ensure no two files remain if paths are identical
            if (
                raw_file_path is not None
                and original_path is not None
                and Path(raw_file_path) == Path(original_path)
            ):
                try:
                    os.remove(str(raw_file_path))
                except OSError as e:
                    logger.warning(
                        "Failed to remove duplicate raw/original report %s: %s",
                        raw_file_path,
                        e,
                    )

            # Remove lock file
            lock_path = Path(str(path_utils.REPORT_DIR) + ".lock")
            try:
                if lock_path.exists():
                    lock_path.unlink()
                    logger.info("Removed lock file during error cleanup: %s", lock_path)
            except Exception as e:
                logger.warning("Could not remove lock file during error cleanup: %s", e)

            # Reset state flags
            if state and processing_context.get("processing_started"):
                state.text_meta_extracted = False
                state.pdf_meta_extracted = False
                state.sensitive_meta_processed = False
                state.anonymized = False
                try:
                    state.save()
                except Exception as e:
                    logger.warning(
                        "Failed to save report state during error cleanup: %s", e
                    )
                logger.debug(
                    "Updated report state to indicate processing failure for %s",
                    getattr(current_pdf, "pdf_hash", None),
                )

        else:
            # Early failure: no current_pdf (or no state).
            # Try to clean up stray files under REPORT_DIR or REPORT_DIR/sensitive.
            pdf_dir = get_pdf_dir()
            raw_dir = original_path.parent if isinstance(original_path, Path) else None

            # Fallback resolution if REPORT_DIR could not be determined
            if not pdf_dir and raw_dir:
                base_dir = raw_dir.parent
                dir_name = getattr(path_utils, "REPORT_DIR_NAME", "pdfs")
                fallback_pdf_dir = base_dir / dir_name
                logger.debug(
                    "report cleanup fallback resolution - base: %s, dir_name: %s, exists: %s",
                    base_dir,
                    dir_name,
                    fallback_pdf_dir.exists(),
                )
                if fallback_pdf_dir.exists():
                    pdf_dir = fallback_pdf_dir

            if pdf_dir and pdf_dir.exists():
                for candidate_dir in (pdf_dir, pdf_dir / "sensitive"):
                    if candidate_dir.exists():
                        for candidate in candidate_dir.glob("*.pdf"):
                            # Don't delete the original ingress file
                            if (
                                original_path is not None
                                and candidate.resolve() == Path(original_path).resolve()
                            ):
                                continue
                            try:
                                candidate.unlink()
                                logger.debug(
                                    "Removed stray report during early error cleanup: %s",
                                    candidate,
                                )
                            except Exception as e:
                                logger.warning(
                                    "Failed to remove stray report %s: %s",
                                    candidate,
                                    e,
                                )

    except Exception as e:
        logger.warning(f"Error during cleanup_on_error body: {e}")
    finally:
        # Remove any sensitive copy created during this processing run
        try:
            sensitive_created = processing_context.get("sensitive_copy_created")
            if sensitive_created:
                pdf_obj = current_pdf
                try:
                    if pdf_obj:
                        file_field = getattr(pdf_obj, "file", None)
                        if file_field and getattr(file_field, "name", None):
                            storage_name = file_field.name
                            file_field.delete(save=False)
                            logger.debug(
                                "Deleted sensitive copy %s during error cleanup",
                                storage_name,
                            )
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to remove sensitive copy during error cleanup: %s",
                        cleanup_exc,
                    )

                pdf_dir = get_pdf_dir()
                if original_path and pdf_dir:
                    # Try to remove any extra file that was created during import
                    for candidate_dir in (pdf_dir, pdf_dir / "sensitive"):
                        candidate = candidate_dir / original_path.name
                        if candidate.exists() and candidate != original_path:
                            try:
                                candidate.unlink()
                                logger.debug(
                                    "Removed stray report copy during error cleanup: %s",
                                    candidate,
                                )
                            except Exception as e:
                                logger.warning(
                                    "Failed to remove stray report copy %s: %s",
                                    candidate,
                                    e,
                                )

            # Always clean up processed_files set to prevent blocks
            file_path = processing_context.get("file_path")
            if file_path and str(file_path) in processed_files:
                processed_files.remove(str(file_path))
                logger.debug(
                    "Removed %s from processed files during error cleanup",
                    file_path,
                )

            # Debug counts for raw/pdf/sensitive dirs
            raw_dir = original_path.parent if isinstance(original_path, Path) else None
            pdf_dir = get_pdf_dir()

            if not pdf_dir and raw_dir:
                base_dir = raw_dir.parent
                dir_name = getattr(path_utils, "REPORT_DIR_NAME", "pdfs")
                fallback_pdf_dir = base_dir / dir_name
                if fallback_pdf_dir.exists():
                    pdf_dir = fallback_pdf_dir

            raw_count = (
                len(list(raw_dir.glob("*"))) if raw_dir and raw_dir.exists() else None
            )
            pdf_count = (
                len(list(pdf_dir.glob("*"))) if pdf_dir and pdf_dir.exists() else None
            )

            sensitive_path = processing_context.get("sensitive_file_path")
            if sensitive_path:
                sensitive_parent = Path(sensitive_path).parent
                sensitive_count = (
                    len(list(sensitive_parent.glob("*")))
                    if sensitive_parent.exists()
                    else None
                )
            else:
                sensitive_dir = pdf_dir / "sensitive" if pdf_dir else None
                sensitive_count = (
                    len(list(sensitive_dir.glob("*")))
                    if sensitive_dir and sensitive_dir.exists()
                    else None
                )

            logger.info(
                "report import error cleanup counts - raw: %s, pdf: %s, sensitive: %s",
                raw_count,
                pdf_count,
                sensitive_count,
            )
        except Exception:
            # Last-resort: never let cleanup throw further up
            pass


def cleanup_processing_context(
    self,
    *,
    processing_context: Dict[str, Any],
    processed_files: MutableSet[str],
) -> None:
    """
    Cleanup processing context after (successful or failed) processing attempt.

    Args:
        processing_context: PdfImportService.processing_context dict
        processed_files: PdfImportService.processed_files set
    """
    try:
        # Clean up temporary directories
        if processing_context.get("text_extracted"):
            crops_dir = path_utils.REPORT_DIR / "cropped_regions"
            if crops_dir.exists() and not any(crops_dir.iterdir()):
                try:
                    crops_dir.rmdir()
                except OSError as e:
                    logger.debug(
                        "Could not remove empty cropped_regions directory %s: %s",
                        crops_dir,
                        e,
                    )

        # Always remove from processed files set after processing attempt
        file_path = processing_context.get("file_path")
        if file_path and str(file_path) in processed_files:
            processed_files.remove(str(file_path))
            logger.debug(
                "Removed %s from processed files set during context cleanup",
                file_path,
            )

    except Exception as e:
        logger.warning(f"Error during context cleanup: {e}")
