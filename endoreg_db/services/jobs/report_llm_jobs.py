from __future__ import annotations
from contextlib import nullcontext
from endoreg_db.services.hub.upload_job_files import (
    record_media_files,
    track_upload_job_files,
)

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol, cast

from django.db import transaction
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject, JsonValue

from endoreg_db.config.env import env_choice, env_int
from endoreg_db.import_files.report_import_service import InvalidReportDocumentError
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.pdf.report_llm_job import (
    ReportLlmInferenceJob,
)
from endoreg_db.models.metadata.sensitive_meta import SensitiveMeta
from endoreg_db.schemas.report_llm import (
    ReportLlmDispatchResult,
    ReportLlmJobConfig,
    ReportLlmJobMode,
    ReportLlmOperation,
    ReportLlmReimportRequestPayload,
    build_report_llm_job_config,
    dump_report_llm_reimport_request_payload,
)
from endoreg_db.services.hub.cleanup import cleanup_upload_job_source
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_completed,
    mark_upload_job_error,
    mark_upload_job_integrity_lost,
    mark_upload_job_processing,
    transition_reimport_upload_jobs,
)
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.raw_pdf_files import require_usable_completed_report
from endoreg_db.services.jobs.error_handling import database_recovery_reason
from endoreg_db.services.reports.import_fencing import (
    ReportImportBusyError,
    StaleReportImportAttemptError,
)
from endoreg_db.services.reports.import_service import ReportImportService
from endoreg_db.utils.api_urls import endoreg_api_path
from endoreg_db.utils.storage import ensure_local_file
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)

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


def _queue_for_report_job(kind: HeavyJobKind) -> str:
    """Route adaptive report work to a worker that is always available."""
    return queue_for_job_kind(kind)


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
    return transition_reimport_upload_jobs(
        _report_upload_jobs(pdf).select_for_update(),
        status=UploadJob.Status.PROCESSING,
    )


def _mark_report_upload_jobs_anonymized(pdf: _RawPdfLike) -> int:
    return transition_reimport_upload_jobs(
        _report_upload_jobs(pdf).select_for_update(),
        status=UploadJob.Status.ANONYMIZED,
        sensitive_meta_id=pdf.sensitive_meta_id,
    )


def _mark_report_upload_jobs_error(pdf: _RawPdfLike, error_detail: str) -> int:
    return transition_reimport_upload_jobs(
        _report_upload_jobs(pdf).select_for_update(),
        status=UploadJob.Status.ERROR,
        error_detail=error_detail,
    )


def _mark_report_upload_jobs_lost(pdf: _RawPdfLike, error_detail: str) -> int:
    return transition_reimport_upload_jobs(
        _report_upload_jobs(pdf).select_for_update(),
        status=UploadJob.Status.LOST,
        error_detail=error_detail,
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
    SensitiveMeta.objects.filter(pk=old_meta_id).delete()
    return int(old_meta_id)


@dataclass
class _ReportJobLifecycle:
    """Job writes share the report's row-locked content ownership boundary."""

    job: ReportLlmInferenceJob
    reimport: bool
    entered: bool = False
    completed: bool = False
    old_meta_id: int | None = None
    processing_upload_jobs: int = 0

    def validate_source(self, content_hash: str) -> None:
        if self.reimport:
            report = self.job.pdf
            if report is None or report.pdf_hash != content_hash:
                raise ValueError("Reimport source does not match the requested content")

    def _refresh_owned_job(self, report: RawPdfFile) -> None:
        self.job = ReportLlmInferenceJob.objects.select_for_update().get(pk=self.job.pk)
        if self.reimport and self.job.pdf != report:
            raise ValueError("Reimport source does not match the requested report")
        if self.job.status == ReportLlmInferenceJob.STATUS_CANCELLED:
            raise ReportImportBusyError("Report job was cancelled")

    def started(self, report: RawPdfFile) -> None:
        self._refresh_owned_job(report)
        if self.job.status == ReportLlmInferenceJob.STATUS_SUCCESS:
            raise ReportImportBusyError(
                "Report job already completed by another delivery"
            )
        self.entered = True
        self.job.mark_running()
        if self.reimport:
            pdf = cast(_RawPdfLike, report)
            self.old_meta_id = _clear_existing_sensitive_meta(pdf)
            self.processing_upload_jobs = _mark_report_upload_jobs_processing(pdf)
        else:
            mark_upload_job_processing(self.upload_job())

    def upload_job(self) -> UploadJob:
        upload_job = self.job.upload_job
        if upload_job is None:
            raise RuntimeError("Report LLM import job has no associated upload job.")
        return upload_job

    def succeeded(self, report: RawPdfFile) -> None:
        self._refresh_owned_job(report)
        if self.job.status == ReportLlmInferenceJob.STATUS_SUCCESS:
            self.completed = True
            return
        self.entered = True
        if self.job.status != ReportLlmInferenceJob.STATUS_RUNNING:
            self.job.mark_running()
        report.refresh_from_db()
        pdf = cast(_RawPdfLike, report)
        processed_file_sha256 = require_usable_completed_report(
            report,
            source_sha256=pdf.pdf_hash,
            require_artifact=False,
        )
        result: JsonObject = {
            "pdf_id": int(pdf.pk),
            "pdf_hash": pdf.pdf_hash,
            "sensitive_meta_id": pdf.sensitive_meta_id,
            "text_extracted": bool(pdf.text),
            "anonymized": bool(pdf.state and pdf.state.anonymized),
            "processed_file_sha256": processed_file_sha256,
        }
        if self.reimport:
            if self.processing_upload_jobs == 0:
                self.processing_upload_jobs = _mark_report_upload_jobs_processing(pdf)
            result.update(
                {
                    "sensitive_meta_created": pdf.sensitive_meta_id is not None,
                    "old_sensitive_meta_id": self.old_meta_id,
                    "processing_upload_jobs": self.processing_upload_jobs,
                    "anonymized_upload_jobs": _mark_report_upload_jobs_anonymized(pdf),
                }
            )
        else:
            upload_job = self.upload_job()
            if upload_job.status != UploadJob.Status.PROCESSING:
                mark_upload_job_processing(upload_job)
            self.job.pdf = report
            self.job.save(update_fields=["pdf", "updated_at"])
            mark_upload_job_completed(upload_job, sensitive_meta=pdf.sensitive_meta)
            result["upload_job_id"] = str(upload_job.pk)
        self.job.mark_success(result=result)
        self.completed = True

    def failed(self, report: RawPdfFile, error: Exception) -> None:
        self._refresh_owned_job(report)
        if self.job.status == ReportLlmInferenceJob.STATUS_SUCCESS:
            raise ReportImportBusyError(
                "Report job already completed by another delivery"
            )
        self.entered = True
        detail = str(error)
        if self.reimport:
            pdf = cast(_RawPdfLike, report)
            if isinstance(error, FileNotFoundError):
                _mark_report_upload_jobs_lost(pdf, detail)
            else:
                _mark_report_upload_jobs_error(pdf, detail)
        elif isinstance(error, FileNotFoundError):
            mark_upload_job_integrity_lost(self.upload_job(), detail)
        else:
            mark_upload_job_error(self.upload_job(), detail)
        if isinstance(error, FileNotFoundError):
            self.job.mark_lost(detail)
        else:
            self.job.mark_failure(detail)


def _run_report_llm_reimport_job(job_id: str) -> bool:
    return _run_report_job(job_id, reimport=True)


def _run_report_llm_import_job(job_id: str) -> bool:
    return _run_report_job(job_id, reimport=False)


def _run_report_job(job_id: str, *, reimport: bool) -> bool:
    job = _get_report_llm_job(job_id)
    if job.status == ReportLlmInferenceJob.STATUS_SUCCESS:
        return True
    if job.status == ReportLlmInferenceJob.STATUS_CANCELLED:
        return False
    lifecycle = _ReportJobLifecycle(job, reimport=reimport)
    try:
        config = ReportLlmJobConfig.model_validate(job.config)
        inventory_job = job.upload_job
        if reimport:
            pdf = job.pdf
            if pdf is None:
                raise RuntimeError("Report LLM job has no associated report.")
            source = pdf.file
            center = pdf.center
            if inventory_job is None:
                previous_import = (
                    ReportLlmInferenceJob.objects.filter(
                        pdf=pdf, upload_job__isnull=False
                    )
                    .select_related("upload_job")
                    .order_by("created_at", "pk")
                    .first()
                )
                if previous_import is not None:
                    inventory_job = previous_import.upload_job
        else:
            upload_job = lifecycle.upload_job()
            source = upload_job.file
            center = upload_job.source_center
        if not source or not source.name:
            raise FileNotFoundError("Report job has no stored source file.")
        if center is None:
            raise RuntimeError("Report job has no resolved source center.")
        inventory = (
            track_upload_job_files(inventory_job)
            if inventory_job is not None
            else nullcontext()
        )
        with inventory, ensure_local_file(source) as file_path:
            report = ReportImportService(lifecycle=lifecycle).import_and_anonymize(
                file_path=file_path,
                center_name=center.name,
                retry=config.retry,
            )
            if inventory_job is not None:
                record_media_files(inventory_job, report)
        if not lifecycle.completed:
            raise RuntimeError("Report service returned without fenced job completion.")
    except (ReportImportBusyError, StaleReportImportAttemptError):
        # The losing delivery has no authority to change the winning job.
        raise
    except Exception as exc:
        if database_recovery_reason(exc) is not None or lifecycle.entered:
            raise
        # Admission failed before a content attempt existed. Only this job's
        # input status is changed; no report metadata or related jobs are reset.
        with transaction.atomic():
            locked_job = ReportLlmInferenceJob.objects.select_for_update().get(
                pk=job.pk
            )
            if locked_job.status != ReportLlmInferenceJob.STATUS_QUEUED:
                raise
            if isinstance(exc, FileNotFoundError):
                locked_job.mark_lost(str(exc))
            else:
                locked_job.mark_failure(str(exc))
            if not reimport:
                upload_job = lifecycle.upload_job()
                if isinstance(exc, InvalidReportDocumentError):
                    upload_job.storage_class = UploadJob.StorageClass.QUARANTINE
                    upload_job.save(update_fields=["storage_class", "updated_at"])
                    mark_upload_job_error(
                        upload_job,
                        str(exc),
                        error_code=UploadJob.ErrorCode.INVALID_INPUT,
                    )
                    emit_structured_event(
                        logger,
                        "report_llm.invalid_document_quarantined",
                        level=logging.ERROR,
                        job_id=job.job_key,
                        content_hash=upload_job.content_hash,
                        failure_class=type(exc).__name__,
                        retryable=False,
                    )
                elif isinstance(exc, FileNotFoundError):
                    mark_upload_job_integrity_lost(upload_job, str(exc))
                else:
                    mark_upload_job_error(upload_job, str(exc))
        raise
    if not reimport:
        cleanup_upload_job_source(lifecycle.upload_job())
    return True


def dispatch_report_llm_reimport(
    *,
    report_id: int,
    payload: ReportLlmReimportRequestPayload,
) -> ReportLlmDispatchResult:
    mode = get_report_llm_job_mode()
    task_id = str(uuid.uuid4())
    queue = _queue_for_report_job(HeavyJobKind.REPORT_LLM_REIMPORT)
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
    queue = _queue_for_report_job(HeavyJobKind.REPORT_LLM_IMPORT)
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
