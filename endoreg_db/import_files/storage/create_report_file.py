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
) -> Tuple[RawPdfFile, str, bool]:
    """
    Create a new or retrieve an existing RawPdfFile for the given context.

    Returns:
        pdf      : RawPdfFile instance
        file_hash: hash used for deduplication
        retry    : whether we are re-processing an existing file
    """
    file_path = ctx.file_path
    center_name = ctx.center_name
    delete_source = ctx.delete_source
    retry = ctx.retry
    file_hash = ctx.file_hash

    existing: RawPdfFile | None = None

    # Try to find existing by hash (if known)
    if file_hash:
        try:
            existing = RawPdfFile.objects.get(pdf_hash=file_hash)
        except RawPdfFile.DoesNotExist:
            existing = None

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
                file_hash=file_hash,
                success=True,
            )
            return existing, file_hash, False

        logger.info(
            "Reprocessing existing report %s (no text found yet)", existing.pdf_hash
        )
        ProcessingHistory.get_or_create_history(
            object_id=existing.pk,
            file_hash=file_hash,
            success=False,
        )
        return existing, file_hash, True

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
            # Explicit retry path: assume file_hash is set
            if not file_hash:
                raise RuntimeError("Retry requested but file_hash is empty")

            pdf = RawPdfFile.objects.get(pdf_hash=file_hash)
            logger.info("Retrying import for existing RawPdfFile %s", pdf.pdf_hash)

            if pdf.text:
                logger.info(
                    "Existing report %s already processed during retry - short-circuiting",
                    pdf.pdf_hash,
                )
                ProcessingHistory.get_or_create_history(
                    object_id=pdf.pk,
                    file_hash=file_hash,
                    success=True,
                )
                return pdf, file_hash, False

        if not pdf:
            raise RuntimeError("Failed to create RawPdfFile instance")

        # Ensure we have a hash even if ctx.file_hash was not set
        if not file_hash:
            file_hash = pdf.pdf_hash

        logger.info("report instance ready: %s", pdf.pdf_hash)

        ProcessingHistory.get_or_create_history(
            object_id=pdf.pk,
            file_hash=file_hash,
            success=bool(getattr(pdf, "text", None)),
        )

        return pdf, file_hash, retry

    except IntegrityError:
        # Race condition - another worker created it first
        if not file_hash:
            raise  # cannot recover without a hash

        pdf = RawPdfFile.objects.get(pdf_hash=file_hash)
        logger.info(
            "Race condition detected, using existing RawPdfFile %s instead",
            pdf.pdf_hash,
        )

        ProcessingHistory.get_or_create_history(
            object_id=pdf.pk,
            file_hash=file_hash,
            success=bool(getattr(pdf, "text", None)),
        )

        return pdf, file_hash, True
