from __future__ import annotations

# Direct boundary coverage intentionally exercises the task/lifecycle adapters.
# pyright: reportPrivateUsage=false
import uuid
import json
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from lx_dtypes.models.contracts.report_anonymization import (
    ReportExtractionEvidence,
    ReportExtractionReceipt,
)

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_llm_job import ReportLlmInferenceJob
from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.schemas.report_llm import ReportImportAttemptReference
from endoreg_db.services.jobs import report_llm_jobs
from endoreg_db.services.reports.completion import (
    ReportCompletionUnverifiedError,
    report_completion_projection,
    require_report_job_completion,
)
from endoreg_db.services.reports.import_fencing import (
    acquire_report_import_fence,
    mark_report_import_fence_failed,
    report_import_finalization_guard,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def completed_job(monkeypatch: pytest.MonkeyPatch) -> ReportLlmInferenceJob:
    report = RawPdfFile.objects.create(
        center=Center.objects.create(name=f"completion-{uuid.uuid4().hex}"),
        pdf_hash="a" * 64,
        file="synthetic.pdf",
    )
    fence = acquire_report_import_fence(report.pdf_hash)
    receipt = ReportExtractionReceipt(
        attempt_id=fence.owner_id.hex,
        source_sha256=report.pdf_hash,
        staged_artifact_sha256="b" * 64,
        extraction_evidence=ReportExtractionEvidence(
            outcome="fallback",
            backend="spacy_regex",
            finding_count=0,
            provider_attempted=False,
        ),
    )
    report.raw_meta = {"report_extraction_receipt": receipt.model_dump(mode="json")}
    report.save(update_fields=["raw_meta"])
    reference = ReportImportAttemptReference(
        content_hash=report.pdf_hash,
        fencing_token=fence.fencing_token,
        owner_id=fence.owner_id.hex,
        extraction_attempt_id=receipt.attempt_id,
        staged_artifact_sha256=receipt.staged_artifact_sha256,
    )
    with report_import_finalization_guard(fence):
        job = ReportLlmInferenceJob.objects.create(
            pdf=report,
            status=ReportLlmInferenceJob.STATUS_SUCCESS,
            config={"kind": "report_llm_reimport", "queue": "pipeline"},
            result={
                "attempt_reference": reference.model_dump(mode="json"),
                "processed_file_sha256": "b" * 64,
            },
        )
    monkeypatch.setattr(
        "endoreg_db.services.reports.completion.require_usable_completed_report",
        Mock(return_value="b" * 64),
    )
    return job


def test_duplicate_completion_projects_native_state_without_mutation(
    completed_job: ReportLlmInferenceJob,
) -> None:
    before = ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk)
    assert report_llm_jobs._run_report_llm_reimport_job(completed_job.job_key)
    assert ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk) == before
    evidence = report_completion_projection(completed_job)
    assert evidence.attempt == "current"
    assert evidence.canonical_status == ReportImportAttempt.STATUS_SUCCEEDED
    assert evidence.extraction == "validated"
    assert report_llm_jobs.report_llm_job_payload(completed_job)[
        "completion_evidence"
    ] == evidence.model_dump(mode="json")


@pytest.mark.parametrize(
    "defect",
    [
        "legacy",
        "missing_attempt",
        "superseded",
        "failed",
        "receipt_missing",
        "receipt_changed",
        "result_artifact_changed",
    ],
)
def test_unverified_delivery_preserves_historical_success(
    completed_job: ReportLlmInferenceJob, defect: str
) -> None:
    report = completed_job.pdf
    assert report is not None
    if defect == "legacy":
        completed_job.result = {"processed_file_sha256": "b" * 64}
        completed_job.save(update_fields=["result"])
    elif defect == "missing_attempt":
        ReportImportAttempt.objects.filter(pk=report.pdf_hash).delete()
    elif defect in {"superseded", "failed"}:
        newer = acquire_report_import_fence(report.pdf_hash)
        if defect == "failed":
            assert mark_report_import_fence_failed(newer)
    elif defect == "receipt_missing":
        report.raw_meta = {}
        report.save(update_fields=["raw_meta"])
    elif defect == "receipt_changed":
        payload = dict(report.raw_meta or {})
        receipt = ReportExtractionReceipt.model_validate_json(
            json.dumps(payload["report_extraction_receipt"])
        )
        payload["report_extraction_receipt"] = receipt.model_copy(
            update={"staged_artifact_sha256": "c" * 64}
        ).model_dump(mode="json")
        report.raw_meta = payload
        report.save(update_fields=["raw_meta"])
    else:
        result = dict(completed_job.result)
        result["processed_file_sha256"] = "c" * 64
        completed_job.result = result
        completed_job.save(update_fields=["result"])
    before = ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk)
    with pytest.raises(ReportCompletionUnverifiedError):
        report_llm_jobs._run_report_llm_reimport_job(completed_job.job_key)
    assert ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk) == before
    assert (
        report_llm_jobs.report_llm_job_payload(completed_job)["status"]
        == ReportLlmInferenceJob.STATUS_SUCCESS
    )


def test_completion_refusal_is_not_retried_or_recorded_as_handoff_failure(
    completed_job: ReportLlmInferenceJob,
) -> None:
    from endoreg_db.tasks import _run_report_task

    error = ReportCompletionUnverifiedError("legacy evidence")
    task = Mock()
    before = ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk)
    with pytest.raises(ReportCompletionUnverifiedError):
        _run_report_task(task, completed_job.job_key, Mock(side_effect=error))
    task.retry.assert_not_called()
    report_llm_jobs._record_celery_handoff_failure(
        job=completed_job,
        operation="report_llm_reimport",
        content_hash="a" * 64,
        retryable=False,
        exc=error,
    )
    assert ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk) == before


@pytest.mark.parametrize("reused", [False, True])
def test_legacy_reuse_is_explicit_and_fresh_missing_receipt_cannot_publish(
    completed_job: ReportLlmInferenceJob,
    monkeypatch: pytest.MonkeyPatch,
    reused: bool,
) -> None:
    report = completed_job.pdf
    assert report is not None
    report.raw_meta = {}
    report.save(update_fields=["raw_meta"])
    job = ReportLlmInferenceJob.objects.create(
        pdf=report,
        status=ReportLlmInferenceJob.STATUS_QUEUED,
        config={"kind": "report_llm_reimport", "queue": "pipeline"},
    )
    monkeypatch.setattr(
        report_llm_jobs, "require_usable_completed_report", Mock(return_value="b" * 64)
    )
    lifecycle = report_llm_jobs._ReportJobLifecycle(job, reimport=True)
    fence = acquire_report_import_fence(report.pdf_hash)
    if reused:
        with report_import_finalization_guard(fence):
            lifecycle.succeeded(report, reused=True)
        job.refresh_from_db()
        require_report_job_completion(job)
        assert report_completion_projection(job).extraction == "legacy_unverified"
        from endoreg_db.schemas.report_llm import ReportLlmJobResult

        reference = ReportLlmJobResult.model_validate(job.result).attempt_reference
        assert reference is not None and reference.reused
    else:
        with (
            pytest.raises(ReportCompletionUnverifiedError),
            report_import_finalization_guard(fence),
        ):
            lifecycle.succeeded(report)
        job.refresh_from_db()
        assert job.status == ReportLlmInferenceJob.STATUS_QUEUED
        assert (
            ReportImportAttempt.objects.get(pk=report.pdf_hash).status
            == ReportImportAttempt.STATUS_ACTIVE
        )


@pytest.mark.parametrize("retry", [False, True])
def test_stale_queued_delivery_does_not_claim_a_new_epoch(
    completed_job: ReportLlmInferenceJob,
    monkeypatch: pytest.MonkeyPatch,
    retry: bool,
) -> None:
    from endoreg_db.import_files.context import ImportContext
    from endoreg_db.import_files.report_import_service import ReportImportService

    report = cast(RawPdfFile, completed_job.pdf)
    winner_before = ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk)
    attempt_before = ReportImportAttempt.objects.values().get(pk=report.pdf_hash)
    # This delivery read queued before the winner committed. The database now
    # contains the winning immutable result; its local object remains stale.
    stale_job = ReportLlmInferenceJob.objects.get(pk=completed_job.pk)
    stale_job.status = ReportLlmInferenceJob.STATUS_QUEUED
    stale_job.config = {
        "kind": "report_llm_reimport",
        "queue": "pipeline",
        "retry": retry,
    }
    monkeypatch.setattr(
        report_llm_jobs,
        "_get_report_llm_job",
        Mock(side_effect=[stale_job, completed_job]),
    )
    monkeypatch.setattr(
        report_llm_jobs,
        "ensure_local_file",
        Mock(return_value=nullcontext(Path("synthetic.pdf"))),
    )
    monkeypatch.setattr(ReportImportService, "_validate_ocr_runtime", Mock())

    def enter_pipeline(
        service: ReportImportService, **kwargs: object
    ) -> RawPdfFile | None:
        ctx = ImportContext(
            file_path=Path("synthetic.pdf"),
            center_name="test",
            file_type="report",
            file_hash=report.pdf_hash,
        )
        if retry:
            return service._run_owned_import(ctx, True)
        return service._reuse_completed_import(ctx, report)

    monkeypatch.setattr(ReportImportService, "import_and_anonymize", enter_pipeline)
    assert report_llm_jobs._run_report_llm_reimport_job(completed_job.job_key)
    assert (
        ReportLlmInferenceJob.objects.values().get(pk=completed_job.pk) == winner_before
    )
    assert (
        ReportImportAttempt.objects.values().get(pk=report.pdf_hash) == attempt_before
    )


def test_fresh_reference_rejects_previous_owner_receipt() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="claimed owner"):
        ReportImportAttemptReference(
            content_hash="a" * 64,
            fencing_token=1,
            owner_id="a" * 32,
            extraction_attempt_id="b" * 32,
            staged_artifact_sha256="c" * 64,
        )
