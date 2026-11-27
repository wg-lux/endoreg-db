from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, cast

from django.db import transaction
from endoreg_db import endoreg_rust_backend as native
from lx_dtypes.models.contracts.pdf_file import PdfFileMetaJsonObject
from endoreg_db.services.secret_rotation.identity import identity_rotation_transaction

from endoreg_db.services.patient_identity.external_ids import (
    assign_patient_external_id,
    split_patient_external_id,
)

from endoreg_db.services.clinical_lifecycle.persistence import (
    advance_workflow_if_journaled,
    require_workflow_transition,
)
from endoreg_db.services.clinical_lifecycle.state_machine import (
    ClinicalLifecycleEvent,
    ClinicalRecordKind,
)
from endoreg_db.services.clinical_lifecycle.facts import report_record_facts
from .integrity import require_usable_completed_report, verify_processed_report_artifact
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


@identity_rotation_transaction
def validate_report_metadata_annotation(
    report: "RawPdfFile",
    extracted_data_dict: PdfFileMetaJsonObject | None = None,
    *,
    delete_original_raw: bool = True,
    enforce_processed_artifact: bool = True,
) -> bool:
    from endoreg_db.services.cases.auto_resolution import correct_inferred_case_identity

    report.refresh_from_db(from_queryset=type(report).objects.select_for_update())
    raw_meta = report.raw_meta or {}
    if not native.media_validation_available(
        bool(getattr(report.state, "processing_error", False)),
        str(raw_meta.get("integrity_status", "")),
    ):
        raise ValueError(
            f"RawPdfFile {report.pdf_hash} is marked failed/lost and cannot be validated."
        )

    if not extracted_data_dict:
        logger.error("No extracted data provided for validation.")
        return False

    processed_file_sha256 = (
        require_usable_completed_report(report) if enforce_processed_artifact else None
    )

    with correct_inferred_case_identity(media_type="pdf", media_obj=report):
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

    mark_report_sensitive_meta_processed(report)
    mark_report_sensitive_meta_verified(report)
    if enforce_processed_artifact:
        advance_workflow_if_journaled(
            ClinicalRecordKind.REPORT,
            int(report.pk),
            ClinicalLifecycleEvent.ANONYMIZATION_VALIDATED,
        )
    get_or_create_raw_pdf_state(report).mark_anonymization_validated()

    if delete_original_raw and processed_file_sha256:
        report_id = report.pk
        approved_names = (report.file.name, report.processed_file.name)

        @transaction.atomic
        def _delete_raw_after_commit() -> None:
            current = type(report).objects.select_for_update().get(pk=report_id)
            if (
                not current.file
                or (current.file.name, current.processed_file.name) != approved_names
            ):
                return
            current_state = current.state
            if current_state is None:
                raise ValueError(
                    "Raw PDF cleanup requires a persisted validation state"
                )
            current_state.refresh_from_db(
                from_queryset=type(current_state).objects.select_for_update()
            )
            if current_state.processed_file_sha256 != processed_file_sha256:
                raise ValueError("Processed report approval changed before raw cleanup")
            facts = report_record_facts(current)
            verify_processed_report_artifact(
                current, expected_sha256=processed_file_sha256
            )
            if facts.direct_identifiers_erased:
                advance_workflow_if_journaled(
                    ClinicalRecordKind.REPORT,
                    int(report_id),
                    ClinicalLifecycleEvent.RAW_PURGED,
                )
            else:
                require_workflow_transition(
                    ClinicalRecordKind.REPORT,
                    int(report_id),
                    facts,
                    ClinicalLifecycleEvent.RAW_PURGED,
                )
            deleted = delete_raw_pdf_raw_file(current, save=True)
            if deleted:
                logger.info(
                    "Deleted raw PDF after report validation commit: report=%s",
                    report_id,
                )

        transaction.on_commit(_delete_raw_after_commit)

    logger.info(
        "Validated report %s with retained processed PDF sha256=%s.",
        report.pk,
        processed_file_sha256,
    )
    return True
