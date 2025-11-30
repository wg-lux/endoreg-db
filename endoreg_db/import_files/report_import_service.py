# endoreg_db/services/report_import_service.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from endoreg_db.import_files.context.file_lock import file_lock
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.import_files.processing.report_processing.report_cleanup_on_error import (
    cleanup_report_on_error,
)
from endoreg_db.import_files.storage.create_report_file import (
    create_or_retrieve_report_file,
)
from endoreg_db.import_files.storage.state_management import (
    finalize_report_success,
    finalize_failure,
    mark_instance_processing_started,
)
from endoreg_db.import_files.context.validate_directories import validate_directories

from endoreg_db.import_files.storage.storage import create_sensitive_copy
from endoreg_db.models.media import RawPdfFile
from endoreg_db.utils.paths import (
    ANONYM_REPORT_DIR,
    IMPORT_REPORT_DIR,
    SENSITIVE_REPORT_DIR,
    TRANSCODING_DIR
)


logger = logging.getLogger(__name__)


class ReportImportService:
    """
    Service for importing and anonymizing report (PDF) files.

    Responsibilities:
      - Acquire file lock
      - Create sensitive copy
      - Create/reuse RawPdfFile (dedupe by hash) + history
      - Run anonymization pipeline (primary + fallback)
      - Finalize state and move anonymized file
      - Cleanup on error
    """

    def __init__(self) -> None:
        self.logger = logger
        self.quarantine_root = Path()
        self.anonymizer = ReportAnonymizer()
        self.processing_context: Optional[ImportContext] = None
        self.current_report: Optional[RawPdfFile] = None
        
        validate_directories()


    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        processor_name: str,
        save_report: bool = True,  # currently unused but kept for API symmetry
        delete_source: bool = True,
    ) -> "RawPdfFile | None":
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            processor_name=processor_name,
            delete_source=delete_source,
        )
        self.processing_context = ctx

        ctx.sensitive_path = create_sensitive_copy(ctx.file_path, SENSITIVE_REPORT_DIR)


        with file_lock(ctx.file_path):
            logger.info("Acquired file lock for %s", ctx.file_path)

            # create or retrieve RawPdfFile + update history
            pdf, retry = create_or_retrieve_report_file(ctx)
            ctx.current_report = pdf
            ctx.retry = retry
            self.current_report = pdf

            mark_instance_processing_started(pdf, ctx)

            try:
                # --- Anonymization with fallback ---
                try:
                    ctx = self.anonymizer.anonymize_report(ctx)
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
                    try:
                        ctx = self.anonymizer.anonymize_report(ctx)
                    except Exception as e:
                        logger.error("PDF Extraction failed for the second time.")
                        raise

                    logger.info(
                        "Basic report anonymization succeeded for %s",
                        ctx.file_path,
                    )

                # --- Finalize success: history + move anonymized file ---
                finalize_report_success(
                    ctx=ctx,
                )

                return pdf

            except Exception as exc:
                logger.exception(
                    "Report import/anonymization failed for %s: %s", ctx.file_path, exc
                )
                # mark failure in history
                finalize_failure(ctx)


