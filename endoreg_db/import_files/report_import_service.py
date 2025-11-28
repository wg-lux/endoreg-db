# endoreg_db/services/report_import_service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union
import logging

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.file_lock import file_lock
from endoreg_db.import_files.storage.create_report_file import create_or_retrieve_report_file
from endoreg_db.import_files.processing.report_processing.report_cleanup_on_error import (
    cleanup_report_on_error,
)
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.import_files.storage.storage import (
    create_sensitive_copy,
)
from endoreg_db.import_files.storage.state_management import (
    finalize_success,
    finalize_report_failure,
    mark_instance_processing_started
)
from endoreg_db.models.media import RawPdfFile
from endoreg_db.utils.paths import (
    STORAGE_DIR,
    SENSITIVE_REPORT_DIR,
    ANONYM_REPORT_DIR,
    RAW_REPORT_DIR
)

logger = logging.getLogger(__name__)


class ReportImportService:
    """
    Service for importing and anonymizing report (PDF) files.

    Responsibilities:
      - Acquire file lock
      - Create sensitive copy
      - Move original into quarantine
      - Create/reuse RawPdfFile (dedupe by hash) + history
      - Run anonymization pipeline (primary + fallback)
      - Finalize state and move anonymized file
      - Cleanup on error
    """

    def __init__(self) -> None:
        self.logger = logger
        self.quarantine_root = Path(STORAGE_DIR / "_processing")
        self.anonymizer = ReportAnonymizer()
        self.processing_context: Optional[ImportContext] = None
        self.current_report: Optional[RawPdfFile] = None

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_report: bool = True,   # currently unused but kept for API symmetry
        delete_source: bool = True,
    ) -> "RawPdfFile | None":
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
        )
        self.processing_context = ctx

        # create sensitive copy *before* quarantine (from original location)
        ctx.sensitive_path = create_sensitive_copy(ctx.file_path, SENSITIVE_REPORT_DIR)

        anonymized_temp_path: Optional[Path] = None

        with file_lock(ctx.file_path):
            logger.info("Acquired file lock for %s", ctx.file_path)

            # move into quarantine
            ctx.quarantine_path = self._move_to_quarantine(ctx.file_path)

            # create or retrieve RawPdfFile + update history
            pdf, file_hash, retry = create_or_retrieve_report_file(ctx)
            ctx.current_report = pdf
            ctx.file_hash = file_hash
            ctx.retry = retry
            self.current_report = pdf
            
            mark_instance_processing_started(pdf, self.ctx)


            try:
                # --- Anonymization with fallback ---
                try:
                    anonymized_temp_path = self.anonymizer.anonymize_report(ctx)
                    logger.info(
                        "Primary report anonymization succeeded for %s",
                        ctx.file_path,
                    )
                except Exception as primary_exc:
                    logger.exception(
                        "Primary report anonymization failed for %s: %s "
                        "- trying basic anonymization",
                        ctx.file_path,
                        primary_exc,
                    )
                    # mark that we're on retry
                    ctx.retry = True
                    anonymized_temp_path = self.anonymizer.basic_anonymize(ctx)
                    logger.info(
                        "Basic report anonymization succeeded for %s",
                        ctx.file_path,
                    )

                # --- Finalize success: history + move anonymized file ---
                finalize_report_success(
                    report=pdf,
                    ctx=ctx,
                    anonymized_root=ANONYM_REPORT_DIR,
                    anonymized_temp_path=anonymized_temp_path,
                )

                # Optional: unquarantine or delete quarantined original
                self._cleanup_quarantine(ctx)

                return pdf

            except Exception as exc:
                logger.exception(
                    "Report import/anonymization failed for %s: %s", ctx.file_path, exc
                )
                # mark failure in history
                finalize_report_failure(pdf, ctx, str(exc))
                # media-specific cleanup while lock is still held
                cleanup_report_on_error(ctx)
                # let caller see the failure
                raise


    def _move_to_quarantine(self, original_path: Path) -> Path:
        from endoreg_db.import_files.context import quarantine

        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        qpath = quarantine(original_path, self.quarantine_root)
        logger.info("Moved %s into quarantine at %s", original_path, qpath)
        return qpath

    def _cleanup_quarantine(self, ctx: ImportContext) -> None:
        """
        Decide what to do with the quarantined file after processing.

        For PDFs you might:
          - delete the quarantined original
          - or move it to a long-term sensitive archive
        """
        from endoreg_db.import_files.context import unquarantine  # local import if needed

        try:
            if ctx.quarantine_path and ctx.delete_source:
                # if delete_source: drop quarantined file
                if ctx.quarantine_path.exists():
                    ctx.quarantine_path.unlink()
                    logger.info(
                        "Deleted quarantined report original at %s",
                        ctx.quarantine_path,
                    )
            else:
                # or move back from quarantine if you want to keep it where it was
                # (adjust this to your semantics or remove if not needed)
                unquarantine(ctx.quarantine_path, RAW_REPORT_DIR)
        except Exception as e:
            logger.warning(
                "Error during quarantine cleanup for %s: %s",
                ctx.file_path,
                e,
            )
