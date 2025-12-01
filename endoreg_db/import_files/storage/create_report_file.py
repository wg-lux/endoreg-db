# endoreg_db/import_files/storage/create_report_file.py
import logging
from typing import Tuple

from django.db import IntegrityError

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models.media import RawPdfFile
from endoreg_db.models.media.storage.processing_history import ProcessingHistory

logger = logging.getLogger(__name__)


def create_or_retrieve_report_file(
    ctx: ImportContext,
) -> Tuple[RawPdfFile, bool]:
    """
    Create a new or retrieve an existing RawPdfFile for the given context.

    Returns:
        pdf      : RawPdfFile instance
        retry    : whether we are re-processing an existing file
    """
    file_path = ctx.file_path
    center_name = ctx.center_name
    delete_source = ctx.delete_source
    retry = ctx.retry
    file_type = ctx.file_type

    existing: RawPdfFile | None = None
    
    if ctx.current_report:
        pk = ctx.current_report.pk
        existing = ctx.current_report.get_pdf_by_pk(pk=pk):
        

    # === NON-RETRY PATH WITH EXISTING FILE ===
    if existing and not retry:
        logger.info("Found existing RawPdfFile %s", existing.pdf_hash)

        if existing.text:
            logger.info(
                "Existing report %s already processed - short-circuiting",
                existing.pdf_hash,
            )
            ProcessingHistory.get_or_create_history(
                object_id=existing.pk,
                file_type=file_type,
                success=True,
            )
            return existing, file_type, False

        logger.info(
            "Reprocessing existing report %s (no text found yet)", existing.pdf_hash
        )
        ProcessingHistory.get_or_create_history(
            object_id=existing.pk,
            file_type=file_type,
            success=False,
        )
        return existing, True

    # === CREATE OR RETRY PATH ===
    logger.info("Creating or retrieving RawPdfFile instance...")

    try:
        if not retry:
            pdf = RawPdfFile.create_from_file_initialized(
                file_path=file_path,
                center_name=center_name,
                delete_source=delete_source,
            )
        else:
            # Explicit retry path: assume file_type is set
            if not file_type:
                raise RuntimeError("Retry requested but file_type is empty")

            pdf = RawPdfFile.objects.get(pdf_hash=file_type)
            logger.info("Retrying import for existing RawPdfFile %s", pdf.pdf_hash)

            if pdf.text:
                logger.info(
                    "Existing report %s already processed during retry - short-circuiting",
                    pdf.pdf_hash,
                )
                ProcessingHistory.get_or_create_history(
                    object_id=pdf.pk,
                    file_type=file_type,
                    success=True,
                )
                return pdf, False

        if not pdf:
            raise RuntimeError("Failed to create RawPdfFile instance")

        # Ensure we have a hash even if ctx.file_type was not set
        if not file_type:
            file_type = pdf.pdf_hash

        logger.info("report instance ready: %s", pdf.pdf_hash)

        ProcessingHistory.get_or_create_history(
            object_id=pdf.pk,
            file_type=file_type,
            success=bool(getattr(pdf, "text", None)),
        )

        return pdf, file_type, retry

    except IntegrityError:
        # Race condition - another worker created it first
        if not file_type:
            raise  # cannot recover without a hash

        pdf = RawPdfFile.objects.get(pdf_hash=file_type)
        logger.info(
            "Race condition detected, using existing RawPdfFile %s instead",
            pdf.pdf_hash,
        )

        ProcessingHistory.get_or_create_history(
            object_id=pdf.pk,
            file_type=file_type,
            success=bool(getattr(pdf, "text", None)),
        )

        return pdf, True
