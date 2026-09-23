from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from pytest import MonkeyPatch

from endoreg_db.config.env import EnvironmentValueError
from endoreg_db.models import Center, RawPdfFile, ReportLlmInferenceJob, UploadJob
from endoreg_db.schemas.report_llm import ReportLlmReimportRequestPayload
from endoreg_db.services.jobs.report_llm_jobs import (
    dispatch_report_llm_import,
    dispatch_report_llm_reimport,
    get_report_llm_dispatch_delay_seconds,
    get_report_llm_job_mode,
    report_llm_job_payload,
)
from endoreg_db.services.raw_pdf_files import ProcessedReportIntegrityError

pytestmark = pytest.mark.django_db


def test_report_llm_job_mode_rejects_unsupported_value(
    monkeypatch: MonkeyPatch,
) -> None:
    raw_value = "sensitive-invalid-report-llm-mode"
    monkeypatch.setenv("REPORT_LLM_JOB_MODE", raw_value)

    with pytest.raises(EnvironmentValueError) as error:
        get_report_llm_job_mode()

    assert error.value.key == "REPORT_LLM_JOB_MODE"
    assert raw_value not in str(error.value)


def test_report_llm_dispatch_delay_rejects_negative_value(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPORT_LLM_DISPATCH_DELAY_SECONDS", "-1")

    with pytest.raises(EnvironmentValueError) as error:
        get_report_llm_dispatch_delay_seconds()

    assert error.value.key == "REPORT_LLM_DISPATCH_DELAY_SECONDS"


@pytest.fixture
def center() -> Center:
    return Center.objects.create(name="Report LLM Job Test Center")


def _make_report(center: Center) -> RawPdfFile:
    return RawPdfFile.objects.create(
        center=center,
        pdf_hash="report-llm-reimport-hash",
        file="reports/report-llm-reimport.pdf",
    )


def _make_upload_job(center: Center) -> UploadJob:
    return UploadJob.objects.create(
        file=SimpleUploadedFile(
            name="report-import.pdf",
            content=b"%PDF-1.4\n%%EOF\n",
            content_type="application/pdf",
        ),
        content_type="application/pdf",
        source_center=center,
        source_system="test",
        processing_provenance={"entrypoint": "test"},
    )


def test_report_reimport_dispatches_to_pipeline_queue(
    monkeypatch: MonkeyPatch, center: Center
) -> None:
    report = _make_report(center)
    captured: dict[str, object] = {}

    def apply_async(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(id="report-llm-reimport-task")

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")
    monkeypatch.setenv("CELERY_PIPELINE_QUEUE", "pipeline")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_report_llm_reimport_task.apply_async",
        apply_async,
    )

    result = dispatch_report_llm_reimport(
        report_id=report.pk,
        payload=ReportLlmReimportRequestPayload(retry=False),
    )

    assert result.status == "queued"
    assert result.operation == ReportLlmInferenceJob.OPERATION_REIMPORT
    assert result.queue == "pipeline"
    assert result.task_id == "report-llm-reimport-task"
    assert result.poll_url is not None
    assert result.poll_url.endswith(f"/llm-jobs/{result.job_id}/")
    captured_kwargs = cast(dict[str, object], captured["kwargs"])
    assert captured_kwargs["queue"] == "pipeline"
    assert captured_kwargs["routing_key"] == "pipeline"
    job = ReportLlmInferenceJob.objects.get(pdf=report)
    assert job.config["request_payload"] == {"retry": False}


def test_report_reimport_duplicate_is_idempotent(
    monkeypatch: MonkeyPatch, center: Center
) -> None:
    report = _make_report(center)
    existing = ReportLlmInferenceJob.objects.create(
        pdf=report,
        operation=ReportLlmInferenceJob.OPERATION_REIMPORT,
        status=ReportLlmInferenceJob.STATUS_QUEUED,
        task_id="already-queued-task",
        queue="llm_inference",
        config={"kind": "report_llm_reimport", "queue": "llm_inference"},
    )
    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")

    result = dispatch_report_llm_reimport(
        report_id=report.pk,
        payload=ReportLlmReimportRequestPayload(),
    )

    assert result.status == "already_queued"
    assert result.task_id == existing.task_id
    assert result.job_id == existing.job_key


def test_report_reimport_recovers_stale_job(
    monkeypatch: MonkeyPatch, center: Center
) -> None:
    report = _make_report(center)
    existing = ReportLlmInferenceJob.objects.create(
        pdf=report,
        operation=ReportLlmInferenceJob.OPERATION_REIMPORT,
        status=ReportLlmInferenceJob.STATUS_RUNNING,
        task_id="stale-task",
        queue="llm_inference",
        config={"kind": "report_llm_reimport", "queue": "llm_inference"},
    )
    ReportLlmInferenceJob.objects.filter(pk=existing.pk).update(
        updated_at=timezone.now() - timedelta(hours=8)
    )
    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "inline")

    def run_reimport(_job_id: int) -> bool:
        return True

    monkeypatch.setattr(
        "endoreg_db.services.jobs.report_llm_jobs._run_report_llm_reimport_job",
        run_reimport,
    )

    result = dispatch_report_llm_reimport(
        report_id=report.pk,
        payload=ReportLlmReimportRequestPayload(),
    )

    existing.refresh_from_db()
    assert existing.status == ReportLlmInferenceJob.STATUS_FAILURE
    assert result.status == "completed"


@pytest.mark.parametrize("llm_enabled", ["true", "false"])
def test_report_upload_import_dispatches_to_pipeline_queue(
    monkeypatch: MonkeyPatch, center: Center, llm_enabled: str
) -> None:
    upload_job = _make_upload_job(center)
    captured_kwargs: dict[str, object] = {}

    def apply_async(*_args: object, **kwargs: object) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return SimpleNamespace(id="report-llm-import-task")

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")
    monkeypatch.setenv("LLM_ENABLED", llm_enabled)
    monkeypatch.setenv("CELERY_PIPELINE_QUEUE", "pipeline")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_report_llm_import_task.apply_async",
        apply_async,
    )

    result = dispatch_report_llm_import(upload_job_id=str(upload_job.pk), payload={})

    assert result.status == "queued"
    assert result.operation == ReportLlmInferenceJob.OPERATION_IMPORT
    assert result.queue == "pipeline"
    assert captured_kwargs["queue"] == "pipeline"
    assert captured_kwargs["routing_key"] == "pipeline"
    assert result.report_id is None
    assert result.poll_url is None
    assert "/pdfs/0/" not in str(result.to_dict())
    assert ReportLlmInferenceJob.objects.filter(upload_job=upload_job).exists()


def test_eager_report_task_failure_is_not_logged_as_dispatch_failure(
    monkeypatch: MonkeyPatch,
    center: Center,
    caplog: pytest.LogCaptureFixture,
) -> None:
    upload_job = _make_upload_job(center)

    class InvalidPdfForTest(ValueError):
        pass

    def apply_async(*_args: object, **kwargs: object) -> SimpleNamespace:
        task_args = cast(tuple[str], kwargs["args"])
        job = ReportLlmInferenceJob.objects.get(job_id=task_args[0])
        job.mark_failure("invalid PDF")
        upload_job.mark_error(
            "invalid PDF",
            error_code=UploadJob.ErrorCode.INVALID_INPUT,
        )
        raise InvalidPdfForTest("invalid PDF")

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_report_llm_import_task.apply_async",
        apply_async,
    )

    with caplog.at_level(
        "ERROR",
        logger="endoreg_db.services.jobs.report_llm_jobs",
    ):
        result = dispatch_report_llm_import(
            upload_job_id=str(upload_job.pk),
            payload={"retry": False},
        )

    assert result.status == "failed"
    structured_events = [
        getattr(record, "structured_event", {}) for record in caplog.records
    ]
    event = next(
        item
        for item in structured_events
        if item.get("event") == "report_llm.task_execution_failed"
    )
    assert event["job_id"] == result.job_id
    assert event["content_hash"] == upload_job.content_hash
    assert event["failure_class"] == "InvalidPdfForTest"
    assert event["retryable"] is False
    assert not any(
        item.get("event") == "report_llm.dispatch_failed" for item in structured_events
    )


def test_report_upload_import_inline_returns_report_poll_url_after_completion(
    monkeypatch: MonkeyPatch,
    center: Center,
) -> None:
    upload_job = _make_upload_job(center)
    report = RawPdfFile.objects.create(
        center=center,
        pdf_hash=f"report-llm-import-completed-{upload_job.pk}",
        file=SimpleUploadedFile(
            name="completed-report-import.pdf",
            content=b"%PDF-1.4\n%%EOF\n",
            content_type="application/pdf",
        ),
    )
    state = report.get_or_create_state()
    state.anonymized = True
    state.sensitive_meta_processed = True
    state.processed_file_sha256 = "a" * 64
    state.save(
        update_fields=[
            "anonymized",
            "sensitive_meta_processed",
            "processed_file_sha256",
            "date_modified",
        ]
    )

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "inline")

    def fake_import_and_anonymize(*_args: object, **_kwargs: object) -> RawPdfFile:
        return report

    monkeypatch.setattr(
        "endoreg_db.services.jobs.report_llm_jobs.ReportImportService.import_and_anonymize",
        fake_import_and_anonymize,
    )

    def fake_require_usable_completed_report(
        *_args: object,
        **_kwargs: object,
    ) -> str:
        return "a" * 64

    monkeypatch.setattr(
        "endoreg_db.services.jobs.report_llm_jobs.require_usable_completed_report",
        fake_require_usable_completed_report,
    )

    result = dispatch_report_llm_import(upload_job_id=str(upload_job.pk), payload={})

    assert result.status == "completed"
    assert result.report_id == report.pk
    assert (
        result.poll_url
        == f"/endoreg-api/media/pdfs/{report.pk}/llm-jobs/{result.job_id}/"
    )

    job = ReportLlmInferenceJob.objects.get(upload_job=upload_job)
    assert getattr(job, "pdf_id") == report.pk
    assert report_llm_job_payload(job)["report_id"] == report.pk
    assert job.result["anonymized"] is True
    assert job.result["processed_file_sha256"] == "a" * 64
    upload_job.refresh_from_db()
    assert upload_job.status == UploadJob.Status.ANONYMIZED


def test_report_upload_import_does_not_publish_success_without_usable_artifact(
    monkeypatch: MonkeyPatch,
    center: Center,
) -> None:
    upload_job = _make_upload_job(center)
    report = RawPdfFile.objects.create(
        center=center,
        pdf_hash=f"report-llm-import-unusable-{upload_job.pk}",
        file=SimpleUploadedFile(
            name="unusable-report-import.pdf",
            content=b"%PDF-1.4\n%%EOF\n",
            content_type="application/pdf",
        ),
    )
    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "inline")

    def fake_import(*_args: object, **_kwargs: object) -> RawPdfFile:
        return report

    monkeypatch.setattr(
        "endoreg_db.services.jobs.report_llm_jobs.ReportImportService.import_and_anonymize",
        fake_import,
    )

    def fail_completion(*_args: object, **_kwargs: object) -> str:
        raise ProcessedReportIntegrityError("processed report missing")

    monkeypatch.setattr(
        "endoreg_db.services.jobs.report_llm_jobs.require_usable_completed_report",
        fail_completion,
    )

    result = dispatch_report_llm_import(upload_job_id=str(upload_job.pk), payload={})

    assert result.status == "failed"
    upload_job.refresh_from_db()
    assert upload_job.status == UploadJob.Status.ERROR
    assert "processed report missing" in upload_job.error_detail


def test_corrupted_pdf_is_quarantined_as_non_retryable_invalid_input(
    monkeypatch: MonkeyPatch,
    center: Center,
) -> None:
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile(
            name="corrupted-report.pdf",
            content=b"%PDF-1.4 topology ingest",
            content_type="application/pdf",
        ),
        content_type="application/pdf",
        source_center=center,
        source_system="test",
        processing_provenance={"entrypoint": "test"},
    )
    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "inline")

    result = dispatch_report_llm_import(
        upload_job_id=str(upload_job.pk),
        payload={"retry": False},
    )

    assert result.status == "failed"
    upload_job.refresh_from_db()
    assert upload_job.status == UploadJob.Status.ERROR
    assert upload_job.error_code == UploadJob.ErrorCode.INVALID_INPUT
    assert upload_job.storage_class == UploadJob.StorageClass.QUARANTINE
    assert upload_job.retryable is False
    assert upload_job.next_retry_at is None
    assert upload_job.file
    assert ReportLlmInferenceJob.objects.filter(upload_job=upload_job).count() == 1
