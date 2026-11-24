import logging
from pathlib import Path
from typing import Protocol, cast

from endoreg_db.import_files.context.ensure_center import ensure_center
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.import_files.file_storage.storage import ensure_context_file_hash
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)
from endoreg_db.services.raw_pdf_files.imports import (
    create_initialized_raw_pdf_file_from_path,
)
from endoreg_db.services.raw_pdf_files.integrity import (
    ProcessedReportIntegrityError,
    require_usable_completed_report,
)

logger = logging.getLogger(__name__)


class _NamedCenter(Protocol):
    name: str


def create_or_retrieve_report_file(
    ctx: ImportContext,
) -> tuple[RawPdfFile, bool, bool]:
    """
    Reuse a completed report or prepare a report for processing.

    Returns:
        A tuple of (report, processed, needs_processing). A usable completed
        report returns (report, True, False). An existing report with only
        failure history returns (report, True, True) to trigger retry handling.
        New reports and unusable completed reports return (report, False, True).
    """
    file_path = ctx.file_path
    if (
        isinstance(ctx.sensitive_path, Path)
        and ctx.sensitive_path.suffix.lower() == ".pdf"
    ):
        file_path = ctx.sensitive_path
    center_name = ctx.center_name
    processed = False

    file_hash = ensure_context_file_hash(ctx)

    has_success_history = ProcessingHistory.has_history_for_hash(
        file_hash=file_hash,
        success=True,
    )
    has_failure_history = ProcessingHistory.has_history_for_hash(
        file_hash=file_hash,
        success=False,
    )
    if ctx.current_report is None:
        ctx.current_report = RawPdfFile.objects.filter(pdf_hash=file_hash).first()
    if has_success_history and ctx.current_report is not None:
        try:
            require_usable_completed_report(
                ctx.current_report, source_sha256=file_hash, require_artifact=False
            )
        except ProcessedReportIntegrityError:
            logger.info("Completed report is unusable; continuing import for repair.")
        else:
            return ctx.current_report, True, False
    elif has_failure_history and ctx.current_report is not None:
        processed = True

    if ctx.current_report is not None:
        report = ctx.current_report
        logger.info("Using existing RawPdfFile from context: pk=%s", report.pk)
    else:
        logger.info(
            "Creating new RawPdfFile from %s for center %s",
            file_path,
            center_name,
        )

        report = create_initialized_raw_pdf_file_from_path(
            file_path=file_path,
            center_name=center_name,
            center_key=ctx.center_key,
        )

        center = cast(
            _NamedCenter,
            ensure_center(report, ctx.center_name, center_key=ctx.center_key),
        )
        logger.info("Successfully set up report file from %s", str(center.name))

    # Retain a non-success history entry until processing completes.
    ProcessingHistory.get_or_create_for_hash(
        obj=report,
        file_hash=file_hash,
        success=False,
    )

    logger.info(
        "Report instance ready for processing: pk=%s, file_type=%s (needs_processing=True)",
        report.pk,
        ctx.file_type,
    )

    return report, processed, True
