# endoreg_db/services/report_import_service.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from endoreg_db.import_files.context.file_lock import file_lock
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.context.validate_directories import validate_directories
from endoreg_db.import_files.file_storage.create_report_file import (
    create_or_retrieve_report_file,
)
from endoreg_db.import_files.file_storage.state_management import (
    finalize_failure,
    finalize_report_success,
    mark_instance_processing_started,
)
from endoreg_db.import_files.file_storage.storage import create_sensitive_copy
from endoreg_db.import_files.processing.report_processing.report_anonymization import (
    ReportAnonymizer,
)
from endoreg_db.models.media import RawPdfFile
from endoreg_db.utils.paths import SENSITIVE_REPORT_DIR

logger = logging.getLogger(__name__)


class ReportImportService:
    """
    Service for importing and anonymizing report (report) files.

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
        self.anonymizer = ReportAnonymizer()
        self.processing_context: Optional[ImportContext] = None
        self.current_report: Optional[RawPdfFile] = None

        validate_directories()

    def import_and_anonymize(
        self,
        file_path: Union[Path, str],
        center_name: str,
        retry: bool = False,
        delete_source: bool = True,
    ) -> "RawPdfFile | None":
        """
        Public entrypoint: wrap import_and_anonymize logic.
        """
        # First, initialize import context. this will be updated during import and keep track of current paths, file type and center and processor.
        ctx = ImportContext(
            file_path=Path(file_path),
            center_name=center_name,
            delete_source=delete_source,
            file_type="report",
            original_path=Path(file_path),
        )
        self.logger.info("validating and preparing file")
        if not ctx.file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        ctx.sensitive_path = create_sensitive_copy(ctx.file_path, SENSITIVE_REPORT_DIR)

        with file_lock(ctx.file_path):
            logger.info("Acquired file lock for %s", ctx.file_path)

            # create or retrieve RawPdfFile + update history
            ctx.current_report, processed, needs_processing = (
                create_or_retrieve_report_file(ctx)
            )
            ctx.current_report.get_or_create_state()
            assert ctx.current_report.state is not None
            ctx.current_report = ctx.current_report

            if processed == True or retry == True:
                ctx.retry = True

            # Retry is a forced overwrite of needs processing - therefore the retry will cause full deletion of processed files using finalize failure.
            if (
                ctx.retry
                and needs_processing
                and not ctx.current_report.state.anonymization_validated
            ):
                # ensure clean slate for forced reprocessing
                finalize_failure(ctx)
                ctx.current_report, processed, needs_processing = (
                    create_or_retrieve_report_file(ctx)
                )
                assert needs_processing is True
            elif not needs_processing and not ctx.retry:
                return ctx.current_report
            else:
                finalize_failure(ctx)
                ctx.current_report, processed, needs_processing = (
                    create_or_retrieve_report_file(ctx)
                )
                assert needs_processing is True

            mark_instance_processing_started(ctx.current_report, ctx)
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
                    try:
                        ctx = self.anonymizer.anonymize_report(ctx)
                    except Exception as e:
                        logger.error(f"report Extraction failed for the second time. {e}")
                        raise

                    logger.info(
                        "Basic report anonymization succeeded for %s",
                        ctx.file_path,
                    )

                # --- Finalize success: history + move anonymized file ---
                finalize_report_success(ctx)

                return ctx.current_report

            except Exception as exc:
                logger.exception(
                    "Report import/anonymization failed for %s: %s", ctx.file_path, exc
                )
                # mark failure in history
                finalize_failure(ctx)
                raise
