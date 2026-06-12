from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from pytest import MonkeyPatch
from django.core.files.uploadedfile import SimpleUploadedFile

from endoreg_db.models import Center, RawPdfFile, ReportLlmInferenceJob, UploadJob
from endoreg_db.services.jobs.report_llm_jobs import (
    dispatch_report_llm_import,
    dispatch_report_llm_reimport,
    report_llm_job_payload,
)

pytestmark = pytest.mark.django_db


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


def test_report_reimport_dispatches_to_llm_queue(
    monkeypatch: MonkeyPatch, center: Center
) -> None:
    report = _make_report(center)
    captured: dict[str, object] = {}

    def apply_async(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(id="report-llm-reimport-task")

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")
    monkeypatch.setenv("CELERY_LLM_INFERENCE_QUEUE", "llm_inference")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_report_llm_reimport_task.apply_async",
        apply_async,
    )

    result = dispatch_report_llm_reimport(report_id=report.pk, payload={})

    assert result.status == "queued"
    assert result.operation == ReportLlmInferenceJob.OPERATION_REIMPORT
    assert result.queue == "llm_inference"
    assert result.task_id == "report-llm-reimport-task"
    assert result.poll_url is not None
    assert result.poll_url.endswith(f"/llm-jobs/{result.job_id}/")
    captured_kwargs = cast(dict[str, object], captured["kwargs"])
    assert captured_kwargs["queue"] == "llm_inference"
    assert captured_kwargs["routing_key"] == "llm_inference"


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

    result = dispatch_report_llm_reimport(report_id=report.pk, payload={})

    assert result.status == "already_queued"
    assert result.task_id == existing.task_id
    assert result.job_id == existing.job_key


def test_report_upload_import_dispatches_to_llm_queue(
    monkeypatch: MonkeyPatch, center: Center
) -> None:
    upload_job = _make_upload_job(center)

    def apply_async(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(id="report-llm-import-task")

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "celery")
    monkeypatch.setattr(
        "endoreg_db.tasks.run_report_llm_import_task.apply_async",
        apply_async,
    )

    result = dispatch_report_llm_import(upload_job_id=str(upload_job.pk), payload={})

    assert result.status == "queued"
    assert result.operation == ReportLlmInferenceJob.OPERATION_IMPORT
    assert result.queue == "llm_inference"
    assert result.report_id is None
    assert result.poll_url is None
    assert "/pdfs/0/" not in str(result.to_dict())
    assert ReportLlmInferenceJob.objects.filter(upload_job=upload_job).exists()


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

    monkeypatch.setenv("REPORT_LLM_JOB_MODE", "inline")

    def fake_import_and_anonymize(*_args: object, **_kwargs: object) -> RawPdfFile:
        return report

    monkeypatch.setattr(
        "endoreg_db.services.jobs.report_llm_jobs.ReportImportService.import_and_anonymize",
        fake_import_and_anonymize,
    )

    result = dispatch_report_llm_import(upload_job_id=str(upload_job.pk), payload={})

    assert result.status == "completed"
    assert result.report_id == report.pk
    assert result.poll_url == f"/api/media/pdfs/{report.pk}/llm-jobs/{result.job_id}/"

    job = ReportLlmInferenceJob.objects.get(upload_job=upload_job)
    assert getattr(job, "pdf_id") == report.pk
    assert report_llm_job_payload(job)["report_id"] == report.pk
