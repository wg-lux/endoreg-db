from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, cast

from django.db import transaction
from lx_dtypes.models.contracts.pdf_file import PdfFileMetaJsonObject

from endoreg_db.services.sensitive_meta_external_ids import (
    assign_patient_external_id,
    split_patient_external_id,
)

from .integrity import require_usable_completed_report
from .io import delete_raw_pdf_raw_file
from .state import (
    get_or_create_raw_pdf_state,
    mark_report_sensitive_meta_processed,
    mark_report_sensitive_meta_verified,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

logger = logging.getLogger(__name__)


class _ValidatedSensitiveMeta(Protocol):
    def update_from_dict(self, data: PdfFileMetaJsonObject) -> None: ...

    def save(self) -> None: ...


def _report_is_failed_or_lost(report: "RawPdfFile") -> bool:
    state = report.state
    raw_meta: dict[str, object]
    if isinstance(report.raw_meta, dict):
        raw_meta = cast(dict[str, object], report.raw_meta)
    else:
        raw_meta = cast(dict[str, object], {})
    return bool(getattr(state, "processing_error", False)) or (
        raw_meta.get("integrity_status") == "lost"
    )


def validate_report_metadata_annotation(
    report: "RawPdfFile",
    extracted_data_dict: PdfFileMetaJsonObject | None = None,
    *,
    delete_original_raw: bool = True,
    enforce_processed_artifact: bool = True,
) -> bool:
    if _report_is_failed_or_lost(report):
        raise ValueError(
            f"RawPdfFile {report.pdf_hash} is marked failed/lost and cannot be validated."
        )

    if not extracted_data_dict:
        logger.error("No extracted data provided for validation.")
        return False

    processed_file_sha256 = (
        require_usable_completed_report(report) if enforce_processed_artifact else None
    )

    sensitive_meta = report.sensitive_meta
    if sensitive_meta is None:
        logger.error("No sensitive meta attached to report %s.", report.pk)
        return False

    model_payload, external_id_pair = split_patient_external_id(extracted_data_dict)
    validated_sensitive_meta = cast(_ValidatedSensitiveMeta, sensitive_meta)
    validated_sensitive_meta.update_from_dict(
        cast(PdfFileMetaJsonObject, model_payload)
    )
    if external_id_pair is not None:
        assign_patient_external_id(
            sensitive_meta=sensitive_meta,
            external_id_pair=external_id_pair,
        )
    validated_sensitive_meta.save()

    report.save()

    logger.info("Metadata for report %s validated and updated successfully.", report.pk)

    get_or_create_raw_pdf_state(report).mark_anonymization_validated()

    mark_report_sensitive_meta_processed(report)
    mark_report_sensitive_meta_verified(report)

    if delete_original_raw:
        report_id = report.pk

        def _delete_raw_after_commit() -> None:
            deleted = delete_raw_pdf_raw_file(report, save=True)
            if deleted:
                logger.info(
                    "Deleted raw PDF after report validation commit: report=%s",
                    report_id,
                )

        transaction.on_commit(_delete_raw_after_commit, robust=True)

    logger.info(
        "Validated report %s with retained processed PDF sha256=%s.",
        report.pk,
        processed_file_sha256,
    )
    return True
