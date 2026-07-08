from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Literal, Protocol, cast

from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_llm_job import (
    ReportLlmInferenceJob,
    ReportLlmJobJsonObject,
    ReportLlmJobJsonValue,
)
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.hub.cleanup import cleanup_upload_job_source
from endoreg_db.services.report_import import ReportImportService
from endoreg_db.utils.api_urls import endoreg_api_path
from endoreg_db.utils.storage import ensure_local_file

logger = logging.getLogger(__name__)

ReportLlmOperation = Literal["report_llm_reimport", "report_llm_import"]
REPORT_LLM_REIMPORT_OPERATION = cast(
    ReportLlmOperation, ReportLlmInferenceJob.OPERATION_REIMPORT
)
REPORT_LLM_IMPORT_OPERATION = cast(
    ReportLlmOperation, ReportLlmInferenceJob.OPERATION_IMPORT
)
REPORT_LLM_JOB_MODE_DEFAULT = "celery"
REPORT_LLM_DISPATCH_DELAY_SECONDS_DEFAULT = 0


JsonValue = ReportLlmJobJsonValue


class _CenterLike(Protocol):
    name: str


class _RawPdfLike(Protocol):
    pk: int
    pdf_hash: str
    center_id: int | None
    center: _CenterLike | None
    file: Any
    sensitive_meta_id: int | None
    sensitive_meta: SensitiveMeta | None
    text: str | None
    anonymized: bool | None

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


class ReportLlmJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["report_llm_reimport", "report_llm_import"]
    queue: str
    retry: bool = True
    request_payload: dict[str, JsonValue] = Field(default_factory=dict)


class ReportLlmDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    mode: str
    status: Literal[
        "queued",
        "already_queued",
        "completed",
        "failed",
        "lost",
    ]
    operation: str
    report_id: int | None = None
    queue: str
    job_id: str
    poll_url: str | None = None
    message: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_none=True)


def _env_int(key: str, default: int) -> int:
    raw_value = os.environ.get(key)
    if raw_value is None:
        return default
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default


def get_report_llm_job_mode() -> str:
    mode = os.environ.get("REPORT_LLM_JOB_MODE", REPORT_LLM_JOB_MODE_DEFAULT)
    normalized = str(mode or REPORT_LLM_JOB_MODE_DEFAULT).strip().lower()
    if normalized not in {"celery", "inline"}:
        logger.warning("Unsupported REPORT_LLM_JOB_MODE=%s; using celery.", mode)
        return REPORT_LLM_JOB_MODE_DEFAULT
    return normalized


def get_report_llm_dispatch_delay_seconds() -> int:
    return max(
        0,
        _env_int(
            "REPORT_LLM_DISPATCH_DELAY_SECONDS",
            REPORT_LLM_DISPATCH_DELAY_SECONDS_DEFAULT,
        ),
    )


def _json_safe(value: Any) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        return [_json_safe(item) for item in sequence]
    return str(value)


def _json_safe_dict(payload: Any) -> ReportLlmJobJsonObject:
    if not hasattr(payload, "items"):
        return {}
    mapping = cast(dict[object, object], payload)
    return {str(key): _json_safe(value) for key, value in mapping.items()}


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
    safe_payload = _json_safe_dict(payload)
    retry = safe_payload.get("retry")
    return ReportLlmJobConfig(
        kind=operation,
        queue=queue,
        retry=True
        if retry is None
        else str(retry).strip().lower() not in {"0", "false", "no"},
        request_payload=safe_payload,
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
        if active_job is not None:
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
        if active_job is not None:
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
    mode: str,
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
                "anonymized": bool(getattr(pdf, "anonymized", False)),
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
        typed_report = cast(_RawPdfLike | None, report)
        sensitive_meta = (
            typed_report.sensitive_meta if typed_report is not None else None
        )
        if typed_report is not None:
            job.pdf = cast(RawPdfFile, typed_report)
            job.save(update_fields=["pdf", "updated_at"])
        upload_job.mark_completed(sensitive_meta=sensitive_meta)
        cleanup_upload_job_source(cast(UploadJob, upload_job))
        result: ReportLlmJobJsonObject = cast(
            ReportLlmJobJsonObject,
            {
                "upload_job_id": str(upload_job.pk),
                "pdf_id": int(typed_report.pk) if typed_report is not None else None,
                "pdf_hash": str(typed_report.pdf_hash)
                if typed_report is not None
                else "",
                "sensitive_meta_id": (
                    int(sensitive_meta.pk) if sensitive_meta is not None else None
                ),
                "text_extracted": bool(getattr(typed_report, "text", "")),
                "anonymized": bool(getattr(typed_report, "anonymized", False)),
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
    except Exception as exc:
        error_detail = str(exc)
        upload_job.mark_error(error_detail)
        job.mark_failure(error_detail)
        logger.exception("Report LLM import job %s failed: %s", job_id, exc)
        raise


def dispatch_report_llm_reimport(
    *,
    report_id: int,
    payload: Any | None = None,
) -> ReportLlmDispatchResult:
    mode = get_report_llm_job_mode()
    task_id = str(uuid.uuid4())
    queue = queue_for_job_kind(HeavyJobKind.REPORT_LLM_REIMPORT)
    operation = REPORT_LLM_REIMPORT_OPERATION
    pdf = RawPdfFile.objects.get(pk=report_id)
    config = _config_from_payload(payload or {}, queue=queue, operation=operation)
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
        logger.exception(
            "Celery dispatch failed for report LLM re-import %s.",
            report_id,
        )
        job.mark_failure(str(exc))
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
        logger.exception(
            "Celery dispatch failed for report LLM import %s.",
            upload_job_id,
        )
        job.mark_failure(str(exc))
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
