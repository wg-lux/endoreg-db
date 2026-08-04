from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from .io import delete_raw_pdf_owned_files
from .state import (
    get_or_create_raw_pdf_state,
    mark_report_sensitive_meta_processed,
    mark_report_sensitive_meta_verified,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

logger = logging.getLogger(__name__)


def _report_is_failed_or_lost(report: "RawPdfFile") -> bool:
    state = report.state
    raw_meta = report.raw_meta if isinstance(report.raw_meta, dict) else {}
    return bool(getattr(state, "processing_error", False)) or (
        raw_meta.get("integrity_status") == "lost"
    )


def validate_report_metadata_annotation(
    report: "RawPdfFile",
    extracted_data_dict: Optional[dict] = None,
) -> bool:
    if _report_is_failed_or_lost(report):
        raise ValueError(
            f"RawPdfFile {report.pdf_hash} is marked failed/lost and cannot be validated."
        )

    if not extracted_data_dict:
        logger.error("No extracted data provided for validation.")
        return False

    sensitive_meta = report.sensitive_meta
    if sensitive_meta is None:
        logger.error("No sensitive meta attached to report %s.", report.pk)
        return False

    sensitive_meta.update_from_dict(extracted_data_dict)
    sensitive_meta.save()

    report.save()

    logger.info("Metadata for report %s validated and updated successfully.", report.pk)

    deleted_original, deleted_anonymized = delete_raw_pdf_owned_files(
        report,
        save=False,
    )
    get_or_create_raw_pdf_state(report).mark_anonymization_validated()

    if deleted_original or deleted_anonymized:
        report.save(update_fields=["file", "processed_file"])

    mark_report_sensitive_meta_processed(report)
    mark_report_sensitive_meta_verified(report)

    logger.info("Files for report %s deleted successfully.", report.pk)
    return True
