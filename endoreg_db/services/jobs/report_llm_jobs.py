from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Literal, Protocol, cast

from django.db import transaction
from django.utils import timezone
from endoreg_db.config.env import env_choice, env_int
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_llm_job import (
    ReportLlmInferenceJob,
    ReportLlmJobJsonObject,
    ReportLlmJobJsonValue,
)
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.schemas.report_llm import (
    ReportLlmDispatchResult,
    ReportLlmJobConfig,
    ReportLlmJobMode,
    ReportLlmReimportRequestPayload,
    build_report_llm_job_config,
    dump_report_llm_reimport_request_payload,
)
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.hub.cleanup import cleanup_upload_job_source
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.import_files.report_import_service import InvalidReportDocumentError
from endoreg_db.services.raw_pdf_files import require_usable_completed_report
from endoreg_db.utils.api_urls import endoreg_api_path
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)

ReportLlmOperation = Literal["report_llm_reimport", "report_llm_import"]
REPORT_LLM_REIMPORT_OPERATION = cast(
    ReportLlmOperation, ReportLlmInferenceJob.OPERATION_REIMPORT
)
REPORT_LLM_IMPORT_OPERATION = cast(
    ReportLlmOperation, ReportLlmInferenceJob.OPERATION_IMPORT
)
REPORT_LLM_JOB_MODE_DEFAULT: ReportLlmJobMode = "celery"
REPORT_LLM_JOB_MODES: tuple[ReportLlmJobMode, ...] = ("celery", "inline")
REPORT_LLM_DISPATCH_DELAY_SECONDS_DEFAULT = 0
REPORT_LLM_STALE_TIMEOUT = timedelta(hours=7)


JsonValue = ReportLlmJobJsonValue


def _record_celery_handoff_failure(
    *,
    job: ReportLlmInferenceJob,
    operation: ReportLlmOperation,
    content_hash: str,
    retryable: bool,
    exc: Exception,
) -> None:
    job.refresh_from_db()
    execution_failed = job.status in {
        ReportLlmInferenceJob.STATUS_FAILURE,
        ReportLlmInferenceJob.STATUS_LOST,
    }
    if not execution_failed:
        job.mark_failure(str(exc))
    emit_structured_event(
        logger,
        (
            "report_llm.task_execution_failed"
            if execution_failed
            else "report_llm.dispatch_failed"
        ),
        level=logging.ERROR,
        job_id=job.job_key,
        operation=operation,
        content_hash=content_hash,
        failure_class=type(exc).__name__,
        retryable=retryable,
    )


class _CenterLike(Protocol):
    name: str


class _RawPdfStateLike(Protocol):
    anonymized: bool
    processed_file_sha256: str


class _RawPdfLike(Protocol):
    pk: int
    pdf_hash: str
    center_id: int | None
    center: _CenterLike | None
    file: Any
    sensitive_meta_id: int | None
    sensitive_meta: SensitiveMeta | None
    text: str | None
    processed_file: Any
    state: _RawPdfStateLike | None

    def save(self, *args: object, **kwargs: object) -> None: ...
    def refresh_from_db(self, *args: object, **kwargs: object) -> None: ...


class _UploadJobLike(Protocol):
    pk: str
    file: Any
    source_center: _CenterLike | None

    def mark_error(self, error_detail: str) -> None: ...
    def mark_lost(self, error_detail: str) -> None: ...
    def mark_processing(self) -> None: ...
    def mark_completed(self, sensitive_meta: SensitiveMeta | None = None) -> None: ...


def get_report_llm_job_mode() -> ReportLlmJobMode:
    return env_choice(
        "REPORT_LLM_JOB_MODE",
        REPORT_LLM_JOB_MODES,
        REPORT_LLM_JOB_MODE_DEFAULT,
    )


def get_report_llm_dispatch_delay_seconds() -> int:
    return env_int(
        "REPORT_LLM_DISPATCH_DELAY_SECONDS",
        REPORT_LLM_DISPATCH_DELAY_SECONDS_DEFAULT,
        minimum=0,
    )


def _report_llm_poll_url(*, report_id: int, job_id: str) -> str:
    return endoreg_api_path(f"media/pdfs/{int(report_id)}/llm-jobs/{job_id}/")


def _report_upload_jobs(pdf: _RawPdfLike):
    queryset = UploadJob.objects.filter(
        content_hash=pdf.pdf_hash,
        content_type="application/pdf",
    )
    center_id = getattr(pdf, "center_id", None)
    if center_id is not None:
        queryset = queryset.filter(source_center_id=center_id)
    return queryset


def _mark_report_upload_jobs_processing(pdf: _RawPdfLike) -> int:
    return _report_upload_jobs(pdf).update(
        status=UploadJob.Status.PROCESSING,
        error_detail="",
        updated_at=timezone.now(),
    )


def _mark_report_upload_jobs_anonymized(pdf: _RawPdfLike) -> int:
    return _report_upload_jobs(pdf).update(
        status=UploadJob.Status.ANONYMIZED,
        error_detail="",
        sensitive_meta_id=pdf.sensitive_meta_id,
        updated_at=timezone.now(),
    )


def _mark_report_upload_jobs_error(pdf: _RawPdfLike, error_detail: str) -> int:
    return _report_upload_jobs(pdf).update(
        status=UploadJob.Status.ERROR,
        error_detail=error_detail,
        updated_at=timezone.now(),
    )


def _mark_report_upload_jobs_lost(pdf: _RawPdfLike, error_detail: str) -> int:
    return _report_upload_jobs(pdf).update(
        status=UploadJob.Status.LOST,
        error_detail=error_detail,
        updated_at=timezone.now(),
    )


def _config_from_payload(
    payload: Any,
    *,
    queue: str,
    operation: ReportLlmOperation,
) -> ReportLlmJobConfig:
    if not isinstance(payload, dict):
        raise ValueError("Report LLM request payload must be a JSON object.")
    return build_report_llm_job_config(
        cast(dict[str, Any], payload),
        queue=queue,
        operation=operation,
    )


def _active_report_llm_jobs(
    *,
    pdf: RawPdfFile,
    operation: str,
):
    return ReportLlmInferenceJob.objects.filter(
        pdf=pdf,
        operation=operation,
        status__in=ReportLlmInferenceJob.ACTIVE_STATUSES,
    ).order_by("created_at", "id")


def _recover_stale_report_llm_job(job: ReportLlmInferenceJob) -> bool:
    if job.updated_at > timezone.now() - REPORT_LLM_STALE_TIMEOUT:
        return False
    job.mark_failure(
        f"Recovered stale report LLM job after {REPORT_LLM_STALE_TIMEOUT}."
    )
    logger.warning("Recovered stale report LLM job: job=%s", job.job_key)
    return True


def _active_upload_report_llm_jobs(
    *,
    upload_job: UploadJob,
    operation: str,
):
    return ReportLlmInferenceJob.objects.filter(
        upload_job=upload_job,
        operation=operation,
        status__in=ReportLlmInferenceJob.ACTIVE_STATUSES,
    ).order_by("created_at", "id")


def _reserve_report_llm_job(
    *,
    pdf: RawPdfFile,
    task_id: str,
    operation: str,
    queue: str,
    config: ReportLlmJobConfig,
) -> tuple[ReportLlmInferenceJob, Literal["created", "already_queued"]]:
    with transaction.atomic():
        locked_pdf = RawPdfFile.objects.select_for_update().get(pk=pdf.pk)
        active_job = (
            _active_report_llm_jobs(pdf=locked_pdf, operation=operation)
            .select_for_update()
            .first()
        )
        if active_job is not None and not _recover_stale_report_llm_job(active_job):
            return active_job, "already_queued"

        job = ReportLlmInferenceJob.objects.create(
            pdf=locked_pdf,
            operation=operation,
            status=ReportLlmInferenceJob.STATUS_QUEUED,
            task_id=task_id,
            queue=queue,
            config=config.model_dump(mode="json"),
        )
        return job, "created"


def _reserve_report_llm_import_job(
    *,
    upload_job: UploadJob,
    task_id: str,
    operation: str,
    queue: str,
    config: ReportLlmJobConfig,
) -> tuple[ReportLlmInferenceJob, Literal["created", "already_queued"]]:
    with transaction.atomic():
        locked_upload_job = UploadJob.objects.select_for_update().get(pk=upload_job.pk)
        active_job = (
            _active_upload_report_llm_jobs(
                upload_job=locked_upload_job,
                operation=operation,
            )
            .select_for_update()
            .first()
        )
        if active_job is not None and not _recover_stale_report_llm_job(active_job):
            return active_job, "already_queued"

        job = ReportLlmInferenceJob.objects.create(
            upload_job=locked_upload_job,
            operation=operation,
            status=ReportLlmInferenceJob.STATUS_QUEUED,
            task_id=task_id,
            queue=queue,
            config=config.model_dump(mode="json"),
        )
        return job, "created"


def _set_report_llm_task_id(job: ReportLlmInferenceJob, task_id: str) -> None:
    if job.task_id == task_id:
        return
    job.task_id = task_id
    job.save(update_fields=["task_id", "updated_at"])


def report_llm_job_payload(job: ReportLlmInferenceJob) -> dict[str, JsonValue]:
    pdf = cast(_RawPdfLike | None, cast(Any, job).pdf)
    if pdf is None:
        report_id = None
    else:
        report_id = int(pdf.pk)
    payload: dict[str, JsonValue] = {
        "status": job.status,
        "operation": job.operation,
        "job_id": job.job_key,
        "task_id": job.task_id,
        "queue": job.queue,
        "report_id": report_id,
        "poll_url": (
            _report_llm_poll_url(report_id=report_id, job_id=job.job_key)
            if report_id is not None
            else None
        ),
        "error": job.error or None,
        "result": job.result or {},
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _dispatch_result(
    *,
    task_id: str,
    mode: ReportLlmJobMode,
    status: Literal["queued", "already_queued", "completed", "failed", "lost"],
    operation: str,
    report_id: int | None,
    queue: str,
    job_id: str,
    message: str | None = None,
    reason: str | None = None,
) -> ReportLlmDispatchResult:
    poll_url = None
    if report_id is not None:
        poll_url = _report_llm_poll_url(report_id=report_id, job_id=job_id)
    return ReportLlmDispatchResult(
        task_id=task_id,
        mode=mode,
        status=status,
        operation=operation,
        report_id=report_id,
        queue=queue,
        job_id=job_id,
        poll_url=poll_url,
        message=message,
        reason=reason,
    )


def _get_report_llm_job(job_id: str) -> ReportLlmInferenceJob:
    return ReportLlmInferenceJob.objects.select_related(
        "pdf",
        "pdf__center",
        "upload_job",
    ).get(job_id=uuid.UUID(str(job_id)))


def _job_report_id(job: ReportLlmInferenceJob) -> int | None:
    pdf = cast(_RawPdfLike | None, cast(Any, job).pdf)
    if pdf is None:
        return None
    return int(pdf.pk)


def _clear_existing_sensitive_meta(pdf: _RawPdfLike) -> int | None:
    old_meta_id = pdf.sensitive_meta_id
    if old_meta_id is None:
        return None

    logger.info(
        "Clearing existing SensitiveMeta %s for report %s",
        old_meta_id,
        pdf.pdf_hash,
    )
    pdf.sensitive_meta = None
    pdf.save(update_fields=["sensitive_meta"])
    try:
        SensitiveMeta.objects.filter(pk=old_meta_id).delete()
    except Exception as exc:
        logger.warning(
            "Could not delete old SensitiveMeta %s for report %s: %s",
            old_meta_id,
            pdf.pdf_hash,
            exc,
        )
    return int(old_meta_id)


def _run_report_llm_reimport_job(job_id: str) -> bool:
    job = _get_report_llm_job(job_id)
    if job.status == ReportLlmInferenceJob.STATUS_SUCCESS:
        return True

    job.mark_running()
    pdf = cast(_RawPdfLike | None, cast(Any, job).pdf)
    if pdf is None:
        error_detail = "Report LLM job has no associated report."
        job.mark_lost(error_detail)
        raise RuntimeError(error_detail)

    try:
        config = ReportLlmJobConfig.model_validate(job.config)
    except Exception as exc:
        error_detail = f"Invalid report LLM job config: {exc}"
        job.mark_failure(error_detail)
        raise RuntimeError(error_detail) from exc

    if not pdf.file or not getattr(pdf.file, "name", None):
        error_detail = (
            "Raw report source is missing. Upload the original report again "
            "before re-importing."
        )
        _mark_report_upload_jobs_lost(pdf, error_detail)
        job.mark_lost(error_detail)
        raise FileNotFoundError(error_detail)

    if not pdf.center:
        error_detail = "Report has no associated center."
        _mark_report_upload_jobs_error(pdf, error_detail)
        job.mark_failure(error_detail)
        raise RuntimeError(error_detail)

    try:
        with transaction.atomic():
            old_meta_id = _clear_existing_sensitive_meta(pdf)
            processing_upload_jobs = _mark_report_upload_jobs_processing(pdf)

        logger.info(
            "Starting report LLM re-import job %s for report %s",
            job.job_key,
            pdf.pdf_hash,
        )
        with ensure_local_file(pdf.file) as raw_file_path:
            ReportImportService().import_and_anonymize(
                file_path=raw_file_path,
                center_name=pdf.center.name,
                retry=config.retry,
            )

        pdf.refresh_from_db()
        processed_file_sha256 = require_usable_completed_report(
            cast(RawPdfFile, pdf),
            source_sha256=pdf.pdf_hash,
        )
        anonymized_upload_jobs = _mark_report_upload_jobs_anonymized(pdf)
        result: ReportLlmJobJsonObject = cast(
            ReportLlmJobJsonObject,
            {
                "pdf_id": int(pdf.pk),
                "pdf_hash": str(pdf.pdf_hash),
                "sensitive_meta_created": pdf.sensitive_meta_id is not None,
                "sensitive_meta_id": int(pdf.sensitive_meta_id)
                if pdf.sensitive_meta_id is not None
                else None,
                "text_extracted": bool(pdf.text),
                "anonymized": bool(pdf.state and pdf.state.anonymized),
                "processed_file_sha256": processed_file_sha256,
                "old_sensitive_meta_id": old_meta_id,
                "processing_upload_jobs": int(processing_upload_jobs),
                "anonymized_upload_jobs": int(anonymized_upload_jobs),
            },
        )
        job.mark_success(result=result)
        logger.info(
            "Report LLM re-import job %s completed for report %s",
            job.job_key,
            pdf.pdf_hash,
        )
        return True
    except FileNotFoundError as exc:
        error_detail = (
            f"Raw report source could not be materialized from storage. {exc}"
        )
        _mark_report_upload_jobs_lost(pdf, error_detail)
        job.mark_lost(error_detail)
        logger.exception("Raw source missing during report LLM re-import %s.", job_id)
        raise
    except Exception as exc:
        error_detail = str(exc)
        _mark_report_upload_jobs_error(pdf, error_detail)
        job.mark_failure(error_detail)
        logger.exception("Report LLM re-import job %s failed: %s", job_id, exc)
        raise


def _run_report_llm_import_job(job_id: str) -> bool:
    job = _get_report_llm_job(job_id)
    if job.status == ReportLlmInferenceJob.STATUS_SUCCESS:
        return True

    job.mark_running()
    upload_job = cast(_UploadJobLike | None, cast(Any, job).upload_job)
    if upload_job is None:
        error_detail = "Report LLM import job has no associated upload job."
        job.mark_lost(error_detail)
        raise RuntimeError(error_detail)

    try:
        config = ReportLlmJobConfig.model_validate(job.config)
    except Exception as exc:
        error_detail = f"Invalid report LLM import job config: {exc}"
        upload_job.mark_error(error_detail)
        job.mark_failure(error_detail)
        raise RuntimeError(error_detail) from exc

    if not upload_job.file or not getattr(upload_job.file, "name", None):
        error_detail = "Upload job has no stored report file."
        upload_job.mark_lost(error_detail)
        job.mark_lost(error_detail)
        raise FileNotFoundError(error_detail)

    center = upload_job.source_center
    if center is None:
        error_detail = "Upload job has no resolved source center."
        upload_job.mark_error(error_detail)
        job.mark_failure(error_detail)
        raise RuntimeError(error_detail)

    upload_job.mark_processing()
    try:
        with ensure_local_file(upload_job.file) as file_path:
            report = ReportImportService().import_and_anonymize(
                file_path=file_path,
                center_name=center.name,
                retry=config.retry,
            )
        if not isinstance(report, RawPdfFile):
            raise RuntimeError("Report import completed without a RawPdfFile result.")
        typed_report = cast(_RawPdfLike, report)
        processed_file_sha256 = require_usable_completed_report(
            report,
            source_sha256=typed_report.pdf_hash,
        )
        sensitive_meta = typed_report.sensitive_meta
        job.pdf = report
        job.save(update_fields=["pdf", "updated_at"])
        upload_job.mark_completed(sensitive_meta=sensitive_meta)
        cleanup_upload_job_source(cast(UploadJob, upload_job))
        result: ReportLlmJobJsonObject = cast(
            ReportLlmJobJsonObject,
            {
                "upload_job_id": str(upload_job.pk),
                "pdf_id": int(typed_report.pk),
                "pdf_hash": str(typed_report.pdf_hash),
                "sensitive_meta_id": (
                    int(sensitive_meta.pk) if sensitive_meta is not None else None
                ),
                "text_extracted": bool(getattr(typed_report, "text", "")),
                "anonymized": bool(
                    typed_report.state and typed_report.state.anonymized
                ),
                "processed_file_sha256": processed_file_sha256,
            },
        )
        job.mark_success(result=result)
        logger.info(
            "Report LLM import job %s completed for upload job %s",
            job.job_key,
            upload_job.pk,
        )
        return True
    except FileNotFoundError as exc:
        error_detail = (
            f"Stored report source could not be materialized from storage. {exc}"
        )
        upload_job.mark_lost(error_detail)
        job.mark_lost(error_detail)
        logger.exception("Stored source missing during report LLM import %s.", job_id)
        raise
    except InvalidReportDocumentError as exc:
        typed_upload_job = cast(UploadJob, upload_job)
        typed_upload_job.storage_class = UploadJob.StorageClass.QUARANTINE
        typed_upload_job.save(update_fields=["storage_class", "updated_at"])
        typed_upload_job.mark_error(
            str(exc),
            error_code=UploadJob.ErrorCode.INVALID_INPUT,
        )
        job.mark_failure(str(exc))
        emit_structured_event(
            logger,
            "report_llm.invalid_document_quarantined",
            level=logging.ERROR,
            job_id=job.job_key,
            content_hash=typed_upload_job.content_hash,
            failure_class=type(exc).__name__,
            retryable=False,
        )
        raise
    except Exception as exc:
        error_detail = str(exc)
        upload_job.mark_error(error_detail)
        job.mark_failure(error_detail)
        logger.exception("Report LLM import job %s failed: %s", job_id, exc)
        raise


def dispatch_report_llm_reimport(
    *,
    report_id: int,
    payload: ReportLlmReimportRequestPayload,
) -> ReportLlmDispatchResult:
    mode = get_report_llm_job_mode()
    task_id = str(uuid.uuid4())
    queue = queue_for_job_kind(HeavyJobKind.REPORT_LLM_REIMPORT)
    operation = REPORT_LLM_REIMPORT_OPERATION
    pdf = RawPdfFile.objects.get(pk=report_id)
    config = _config_from_payload(
        dump_report_llm_reimport_request_payload(payload),
        queue=queue,
        operation=operation,
    )
    job, reservation_status = _reserve_report_llm_job(
        pdf=pdf,
        task_id=task_id,
        operation=operation,
        queue=queue,
        config=config,
    )

    if reservation_status == "already_queued":
        return _dispatch_result(
            task_id=job.task_id or "",
            mode=mode,
            status="already_queued",
            operation=operation,
            report_id=int(report_id),
            queue=queue,
            job_id=job.job_key,
            message="Report LLM re-import is already queued or running.",
        )

    if mode == "inline":
        try:
            completed = _run_report_llm_reimport_job(job.job_key)
        except FileNotFoundError as exc:
            return _dispatch_result(
                task_id=task_id,
                mode=mode,
                status="lost",
                operation=operation,
                report_id=int(report_id),
                queue=queue,
                job_id=job.job_key,
                reason=str(exc),
            )
        except Exception as exc:
            return _dispatch_result(
                task_id=task_id,
                mode=mode,
                status="failed",
                operation=operation,
                report_id=int(report_id),
                queue=queue,
                job_id=job.job_key,
                reason=str(exc),
            )
        return _dispatch_result(
            task_id=task_id,
            mode=mode,
            status="completed" if completed else "failed",
            operation=operation,
            report_id=int(report_id),
            queue=queue,
            job_id=job.job_key,
        )

    try:
        from endoreg_db.tasks import run_report_llm_reimport_task

        ensure_secure_transport_for_job_kind(HeavyJobKind.REPORT_LLM_REIMPORT)
        async_result = run_report_llm_reimport_task.apply_async(
            args=(job.job_key,),
            queue=queue,
            routing_key=queue,
            countdown=get_report_llm_dispatch_delay_seconds(),
        )
        _set_report_llm_task_id(job, str(async_result.id))
        return _dispatch_result(
            task_id=str(async_result.id),
            mode=mode,
            status="queued",
            operation=operation,
            report_id=int(report_id),
            queue=queue,
            job_id=job.job_key,
            message="Report LLM re-import queued.",
        )
    except Exception as exc:
        _record_celery_handoff_failure(
            job=job,
            operation=operation,
            content_hash=pdf.pdf_hash,
            retryable=False,
            exc=exc,
        )
        return _dispatch_result(
            task_id=task_id,
            mode=mode,
            status="failed",
            operation=operation,
            report_id=int(report_id),
            queue=queue,
            job_id=job.job_key,
            reason=str(exc),
        )


def dispatch_report_llm_import(
    *,
    upload_job_id: str,
    payload: Any | None = None,
) -> ReportLlmDispatchResult:
    mode = get_report_llm_job_mode()
    task_id = str(uuid.uuid4())
    queue = queue_for_job_kind(HeavyJobKind.REPORT_LLM_IMPORT)
    operation = REPORT_LLM_IMPORT_OPERATION
    upload_job = UploadJob.objects.get(pk=upload_job_id)
    config = _config_from_payload(payload or {}, queue=queue, operation=operation)
    job, reservation_status = _reserve_report_llm_import_job(
        upload_job=upload_job,
        task_id=task_id,
        operation=operation,
        queue=queue,
        config=config,
    )

    if reservation_status == "already_queued":
        return _dispatch_result(
            task_id=job.task_id or "",
            mode=mode,
            status="already_queued",
            operation=operation,
            report_id=_job_report_id(job),
            queue=queue,
            job_id=job.job_key,
            message="Report LLM import is already queued or running.",
        )

    if mode == "inline":
        try:
            completed = _run_report_llm_import_job(job.job_key)
        except FileNotFoundError as exc:
            return _dispatch_result(
                task_id=task_id,
                mode=mode,
                status="lost",
                operation=operation,
                report_id=_job_report_id(job),
                queue=queue,
                job_id=job.job_key,
                reason=str(exc),
            )
        except Exception as exc:
            return _dispatch_result(
                task_id=task_id,
                mode=mode,
                status="failed",
                operation=operation,
                report_id=_job_report_id(job),
                queue=queue,
                job_id=job.job_key,
                reason=str(exc),
            )
        job.refresh_from_db()
        return _dispatch_result(
            task_id=task_id,
            mode=mode,
            status="completed" if completed else "failed",
            operation=operation,
            report_id=_job_report_id(job),
            queue=queue,
            job_id=job.job_key,
        )

    try:
        from endoreg_db.tasks import run_report_llm_import_task

        ensure_secure_transport_for_job_kind(HeavyJobKind.REPORT_LLM_IMPORT)
        async_result = run_report_llm_import_task.apply_async(
            args=(job.job_key,),
            queue=queue,
            routing_key=queue,
            countdown=get_report_llm_dispatch_delay_seconds(),
        )
        _set_report_llm_task_id(job, str(async_result.id))
        return _dispatch_result(
            task_id=str(async_result.id),
            mode=mode,
            status="queued",
            operation=operation,
            report_id=_job_report_id(job),
            queue=queue,
            job_id=job.job_key,
            message="Report LLM import queued.",
        )
    except Exception as exc:
        upload_job.refresh_from_db()
        _record_celery_handoff_failure(
            job=job,
            operation=operation,
            content_hash=upload_job.content_hash,
            retryable=upload_job.retryable,
            exc=exc,
        )
        return _dispatch_result(
            task_id=task_id,
            mode=mode,
            status="failed",
            operation=operation,
            report_id=_job_report_id(job),
            queue=queue,
            job_id=job.job_key,
            reason=str(exc),
        )
