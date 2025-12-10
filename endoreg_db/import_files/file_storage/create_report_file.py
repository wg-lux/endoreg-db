# endoreg_db/import_files/storage/create_report_file.py
import logging
from typing import Tuple

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import RawPdfFile
from endoreg_db.models.media.processing_history.processing_history import ProcessingHistory
from endoreg_db.import_files.context.ensure_center import ensure_center
logger = logging.getLogger(__name__)


def create_or_retrieve_report_file(
    ctx: ImportContext,
) -> Tuple[RawPdfFile, bool]:
    """
    Create a new or retrieve an existing RawPdfFile for the given context.

    Returns:
        pdf             : RawPdfFile instance
        needs_processing: True if the pipeline should run for this file
                          (no successful history yet for this object/file_type key)
    """
    file_path = ctx.file_path
    center_name = ctx.center_name
    delete_source = ctx.delete_source
    file_type = ctx.file_type  # logical key for history; can be None

    # 1) Determine the RawPdfFile instance to work with
    if ctx.current_report is not None:
        pdf = ctx.current_report
        logger.info("Using existing RawPdfFile from context: pk=%s", pdf.pk)
    else:
        logger.info("Creating new RawPdfFile from %s for center %s", file_path, center_name)
        
        pdf = RawPdfFile.create_from_file_initialized(
            file_path=file_path,
            center_name=center_name,
            delete_source=delete_source,
        )
        
        center = ensure_center(pdf, ctx.center_name)
        
        logger.info(f"Successfully set up report file from {center.name}")



    # 3) Check if we already have a successful history entry for this object+file_type
    has_success_history = ProcessingHistory.has_history_for_object(
        obj=pdf,
        success=True,
    )

    if has_success_history:
        logger.info(
            "RawPdfFile %s already has successful processing history (file_type=%s) - short-circuiting",
            getattr(pdf, str(pdf.file_path)),
            file_type,
        )
        # No need to run the pipeline again
        return pdf, False

    # 4) No successful history yet → ensure there is a history entry marking it as "in progress"/failed
    ProcessingHistory.get_or_create_for_object(
        obj=pdf,
        # At this point we haven't finished anonymization; treat as not-success yet.
        success=False,
    )

    logger.info(
        "Report instance ready for processing: pk=%s, file_type=%s (needs_processing=True)",
        pdf.pk,
        file_type,
    )

    # Signal to the caller that the anonymization pipeline should run
    return pdf, True
