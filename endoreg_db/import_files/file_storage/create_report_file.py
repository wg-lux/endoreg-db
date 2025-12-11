# endoreg_db/import_files/storage/create_report_file.py
import logging
from pathlib import Path
from typing import Tuple

from endoreg_db.import_files.context.ensure_center import ensure_center
from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import RawPdfFile
from endoreg_db.models.state.processing_history.processing_history import (
    ProcessingHistory,
)

logger = logging.getLogger(__name__)


def create_or_retrieve_report_file(
    ctx: ImportContext,
) -> Tuple[RawPdfFile, bool, bool]:
    """
    Create a new or retrieve an existing RawPdfFile for the given context.

    Returns:
        pdf             : RawPdfFile instance
        processed       : True if there is already a successful ProcessingHistory for this file
        needs_processing: True if the pipeline should run for this file in this call
    """
    file_path = ctx.file_path
    center_name = ctx.center_name
    delete_source = ctx.delete_source
    file_type = ctx.file_type  # logical key for history; can be None

    # default assumptions
    processed = False
    needs_processing = True

    # 1) Determine the RawPdfFile instance to work with
    if ctx.current_report is not None:
        pdf = ctx.current_report
        logger.info("Using existing RawPdfFile from context: pk=%s", pdf.pk)
    else:
        logger.info(
            "Creating new RawPdfFile from %s for center %s",
            file_path,
            center_name,
        )

        pdf = RawPdfFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            delete_source=delete_source,
        )

        center = ensure_center(pdf, ctx.center_name)
        logger.info("Successfully set up report file from %s", center.name)

    # 2) Check if we already have a successful history entry for this object
    has_success_history = ProcessingHistory.has_history_for_hash(
        file_hash=ctx.file_hash,
        success=True,
    )

    if has_success_history:
        logger.info(
            "RawPdfFile pk=%s already has successful processing history (file_type=%s) - short-circuiting",
            pdf.pk,
            file_type,
        )
        processed = True
        needs_processing = False
        return pdf, processed, needs_processing

    # 3) No successful history yet → ensure there is a history entry marking it as "in progress"/failed
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
