# endoreg_db/import_files/storage/create_report_file.py
import logging
from pathlib import Path
from typing import Protocol, cast

from endoreg_db.import_files.context.ensure_center import ensure_center
from endoreg_db.import_files.context.import_context import ImportContext  #
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.services.raw_pdf_files.imports import (
    create_initialized_raw_pdf_file_from_path,
)
from endoreg_db.services.raw_pdf_files.integrity import (
    ProcessedReportIntegrityError,
    require_usable_completed_report,
)
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)

logger = logging.getLogger(__name__)


class _NamedCenter(Protocol):
    name: str


def create_or_retrieve_report_file(
    ctx: ImportContext,
) -> tuple[RawPdfFile, bool, bool]:
    """
    Create a new or retrieve an existing RawPdfFile for the given context.

    Returns:
        pdf             : RawPdfFile instance
        processed       : True if there is already a successful ProcessingHistory for this file
        needs_processing: True if the pipeline should run for this file in this call
    """
    file_path = ctx.file_path
    if (
        isinstance(ctx.sensitive_path, Path)
        and ctx.sensitive_path.suffix.lower() == ".pdf"
    ):
        file_path = ctx.sensitive_path
    center_name = ctx.center_name
    file_type = ctx.file_type  # logical key for history; can be None

    # default assumptions
    processed = False
    needs_processing = True

    if not isinstance(ctx.file_hash, str):
        ctx.file_hash = get_file_hash(ctx.file_path)

    # Check if we already have a successful history entry for this object
    has_success_history = ProcessingHistory.has_history_for_hash(
        file_hash=ctx.file_hash,
        success=True,
    )
    has_failure_history = ProcessingHistory.has_history_for_hash(
        file_hash=ctx.file_hash,
        success=False,
    )
    if ctx.current_report is None:
        ctx.current_report = RawPdfFile.objects.filter(pdf_hash=ctx.file_hash).first()
    if has_success_history and ctx.current_report is not None:
        try:
            require_usable_completed_report(
                ctx.current_report, source_sha256=ctx.file_hash, require_artifact=False
            )
        except ProcessedReportIntegrityError:
            logger.info("Completed report is unusable; continuing import for repair.")
        else:
            return ctx.current_report, True, False
    elif has_failure_history and ctx.current_report is not None:
        processed = True

    # Determine the RawPdfFile instance to work with
    if ctx.current_report is not None:
        pdf = ctx.current_report
        logger.info("Using existing RawPdfFile from context: pk=%s", pdf.pk)
    else:
        logger.info(
            "Creating new RawPdfFile from %s for center %s",
            file_path,
            center_name,
        )

        pdf = create_initialized_raw_pdf_file_from_path(
            file_path=file_path,
            center_name=center_name,
        )

        center = cast(_NamedCenter, ensure_center(pdf, ctx.center_name))
        center_name_value = str(center.name)
        logger.info("Successfully set up report file from %s", center_name_value)

    # No successful history yet → ensure there is a history entry marking it as "in progress"/failed
    ProcessingHistory.get_or_create_for_hash(
        obj=pdf,
        file_hash=ctx.file_hash,
        success=False,
    )

    logger.info(
        "Report instance ready for processing: pk=%s, file_type=%s (needs_processing=True)",
        pdf.pk,
        file_type,
    )

    return pdf, processed, needs_processing
