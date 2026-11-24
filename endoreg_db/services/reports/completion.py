"""Read-only report completion evidence projected from the canonical native attempt."""

import json
from typing import cast

from django.db import transaction
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile

from pydantic import ValidationError
from lx_dtypes.models.contracts.report_anonymization import ReportExtractionReceipt

from endoreg_db.models.media.pdf.report_llm_job import ReportLlmInferenceJob
from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.schemas.report_llm import (
    ReportCompletionProjection,
    ReportLlmJobResult,
)
from endoreg_db.services.raw_pdf_files import require_usable_completed_report


class ReportCompletionUnverifiedError(RuntimeError):
    """Delivery completion lacks current evidence; never mutate or retry history."""


def report_completion_projection(
    job: ReportLlmInferenceJob,
) -> ReportCompletionProjection:
    try:
        result = ReportLlmJobResult.model_validate(job.result or {})
    except ValidationError:
        return ReportCompletionProjection(attempt="invalid", extraction="invalid")
    reference = result.attempt_reference
    if reference is None:
        return ReportCompletionProjection(
            attempt="legacy_unverified"
            if job.status == ReportLlmInferenceJob.STATUS_SUCCESS
            else "not_recorded"
        )
    report = cast(RawPdfFile | None, job.pdf)
    if report is None or report.pdf_hash != reference.content_hash:
        return ReportCompletionProjection(attempt="invalid", extraction="invalid")
    attempt = ReportImportAttempt.objects.filter(
        content_hash=reference.content_hash
    ).first()
    if attempt is None:
        return ReportCompletionProjection(attempt="missing")
    if attempt.fencing_token != reference.fencing_token:
        return ReportCompletionProjection(attempt="superseded")
    # No state reduction here: expose precisely the canonical persisted value.
    status = str(attempt.status)
    receipt_payload = (report.raw_meta or {}).get("report_extraction_receipt")
    if receipt_payload is None:
        legacy_reuse = (
            reference.reused
            and reference.extraction_attempt_id is None
            and reference.staged_artifact_sha256 is None
        )
        return ReportCompletionProjection(
            attempt="current",
            canonical_status=status,
            extraction="legacy_unverified" if legacy_reuse else "invalid",
        )
    try:
        receipt = ReportExtractionReceipt.model_validate_json(
            json.dumps(receipt_payload)
        )
    except (ValidationError, TypeError, ValueError):
        return ReportCompletionProjection(
            attempt="current", canonical_status=status, extraction="invalid"
        )
    if (
        receipt.source_sha256 != reference.content_hash
        or receipt.staged_artifact_sha256 != result.processed_file_sha256
        or receipt.attempt_id != reference.extraction_attempt_id
        or receipt.staged_artifact_sha256 != reference.staged_artifact_sha256
    ):
        return ReportCompletionProjection(
            attempt="current", canonical_status=status, extraction="invalid"
        )
    evidence = receipt.extraction_evidence
    return ReportCompletionProjection(
        attempt="current",
        canonical_status=status,
        extraction="validated",
        extraction_outcome=evidence.outcome,
        provider_failure=evidence.provider_failure.value
        if evidence.provider_failure
        else None,
    )


@transaction.atomic
def require_report_job_completion(job: ReportLlmInferenceJob) -> None:
    """A successful task is a projection of committed native state and evidence."""
    report = cast(RawPdfFile | None, job.pdf)
    if report is not None:
        ReportImportAttempt.objects.select_for_update().filter(
            content_hash=report.pdf_hash
        ).first()
        report.refresh_from_db()
    projection = report_completion_projection(job)
    if (
        job.status != ReportLlmInferenceJob.STATUS_SUCCESS
        or projection.attempt != "current"
        or projection.canonical_status != ReportImportAttempt.STATUS_SUCCEEDED
        or projection.extraction == "invalid"
    ):
        raise ReportCompletionUnverifiedError(
            "Report completion is unverified "
            f"(attempt={projection.attempt}, extraction={projection.extraction})"
        )
    report = cast(RawPdfFile | None, job.pdf)
    if report is None:
        raise ReportCompletionUnverifiedError("Report completion has no report")
    digest = require_usable_completed_report(
        report, source_sha256=report.pdf_hash, require_artifact=False
    )
    result = ReportLlmJobResult.model_validate(job.result)
    if digest != result.processed_file_sha256:
        raise ReportCompletionUnverifiedError(
            "Report completion artifact identity changed"
        )
