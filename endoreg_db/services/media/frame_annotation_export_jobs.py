"""Browser-started frame annotation dataset exports run as Celery jobs.

The request body is the lx_dtypes YAML export contract
(``VideoFrameAnnotationExportConfigPayload``), shared with
``scripts/export_frame_annotations.py``; execution delegates to
``AnnotationExporterClient.run_export``.

Every job has exactly one center scope: center staff may export their own
centers, while all-center exports are limited to staff and superusers.

Each job writes one run directory, a single path segment directly beneath the
protected frame annotation export root, so run directories never nest. The
worker holds a process-owned advisory lock on that run directory from claim to
completion; the kernel releases it when the process dies. That lock, not the
database status, excludes concurrent writers:

* lock busy, job running: duplicate Celery delivery, nothing changes;
* lock free, job running: the previous worker died, the job becomes lost;
* lock busy, job queued: a lost but still running writer owns the directory,
  the job fails instead of writing in parallel.

Status writes after the claim are fenced by the claim token, so a stale worker
cannot overwrite a lost or failed job.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import QuerySet
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerOperationalError
from lx_dtypes.models.contracts.json_types import JsonValue
from lx_dtypes.models.contracts.video_frame_export import (
    VideoFrameAnnotationExportConfigPayload,
    export_config,
    export_result,
)
from pydantic import ValidationError as PydanticValidationError

from endoreg_db.export.frames.export_frames_with_labels import (
    AnnotationExporterClient,
    ExportJobFailedError,
)
from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.media.video.frame_annotation_export_job import (
    FrameAnnotationExportJob,
)
from endoreg_db.schemas.frame_annotation_export_job import (
    FrameAnnotationExportJobResult,
    dump_frame_annotation_export_job_result,
    validate_frame_annotation_export_job_config,
)
from endoreg_db.services.centers.access import resolve_allowed_center_ids
from endoreg_db.services.jobs.heavy_jobs import (
    HeavyJobKind,
    ensure_secure_transport_for_job_kind,
    queue_for_job_kind,
)
from endoreg_db.services.media.export_annotated import (
    ExportConflictError,
    confine_export_location,
)
from endoreg_db.utils.file_operations import advisory_file_lock
from endoreg_db.utils.paths import ensure_within_storage_root, get_runtime_paths

logger = logging.getLogger(__name__)

FRAME_ANNOTATION_EXPORT_HEARTBEAT_INTERVAL = timedelta(seconds=60)
# A running job without heartbeat for this long lost its worker.
FRAME_ANNOTATION_EXPORT_RUNNING_STALE_AFTER = timedelta(minutes=10)
# A queued job no worker picked up for this long will not start reliably.
FRAME_ANNOTATION_EXPORT_QUEUED_STALE_AFTER = timedelta(hours=24)
# Run locks live beside the run directories; run names cannot start with a dot.
FRAME_ANNOTATION_EXPORT_LOCK_DIR_NAME = ".locks"

_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

type ExportUser = User | AnonymousUser


@dataclass(frozen=True, slots=True)
class PreparedFrameAnnotationExport:
    """Validated config with its resolved scope and confined run directory."""

    payload: VideoFrameAnnotationExportConfigPayload
    center: Center | None
    run_name: str


def prepare_frame_annotation_export(
    payload: Mapping[str, JsonValue], *, user: ExportUser
) -> PreparedFrameAnnotationExport:
    """Validate contract, center scope and output location without side effects.

    Raises ``pydantic.ValidationError`` for contract violations,
    ``ValidationError`` for invalid scope or location and ``PermissionDenied``
    for scopes outside the user's centers.
    """
    if not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Export jobs require an active authenticated user.")
    config = validate_frame_annotation_export_job_config(dict(payload))
    if config.load_base_data:
        raise ValidationError(
            "load_base_data cannot be started from the browser; "
            "run scripts/export_frame_annotations.py instead."
        )
    run_name = _single_segment("output_dir", config.output_dir)
    _single_segment("output_path", config.output_path)
    center = _resolve_scope(config, user=user)
    _confine_run(run_name, config.output_path)
    return PreparedFrameAnnotationExport(
        payload=config, center=center, run_name=run_name
    )


def create_frame_annotation_export_job(
    payload: Mapping[str, JsonValue], *, user: User
) -> FrameAnnotationExportJob:
    """Persist a queued job and dispatch it after commit.

    Raises ``ExportConflictError`` while another job owns the run directory.
    """
    prepared = prepare_frame_annotation_export(payload, user=user)
    recover_stale_frame_annotation_export_jobs()
    try:
        with transaction.atomic():
            job = FrameAnnotationExportJob.objects.create(
                requested_by=user,
                center=prepared.center,
                all_centers=prepared.payload.all_centers,
                output_dir=prepared.run_name,
                config=prepared.payload,
            )
    except IntegrityError as exc:
        raise ExportConflictError(
            f"export_in_progress: another export is writing {prepared.run_name}"
        ) from exc
    transaction.on_commit(lambda: _dispatch(job))
    return job


def run_frame_annotation_export_job(
    job_id: UUID | str,
    *,
    client: AnnotationExporterClient | None = None,
) -> FrameAnnotationExportJob:
    """Execute one job under its run lock; the Celery task is the production caller."""
    job = FrameAnnotationExportJob.objects.get(pk=UUID(str(job_id)))
    with ExitStack() as stack:
        try:
            stack.enter_context(
                advisory_file_lock(
                    lock_path=run_lock_path(job.output_dir), timeout_seconds=0
                )
            )
        except TimeoutError:
            return _run_lock_busy(job)

        token = _claim(job)
        if token is None:
            job.refresh_from_db()
            return job

        # Scope and location may have changed while the job waited in the queue.
        try:
            config = _export_config(job)
        except (ValidationError, PermissionDenied, PydanticValidationError) as exc:
            return _finish(
                job,
                token,
                status=FrameAnnotationExportJob.Status.FAILED,
                error=str(exc),
            )

        stop_heartbeat = _start_heartbeat(job.pk, token)
        try:
            result = (client or AnnotationExporterClient()).run_export(config)
        # run_export wraps unexpected failures in ExportJobFailedError; anything
        # else propagates and the missing heartbeat marks the job lost.
        except (ExportJobFailedError, ValueError) as exc:
            logger.warning("Frame annotation export job %s failed: %s", job.pk, exc)
            return _finish(
                job,
                token,
                status=FrameAnnotationExportJob.Status.FAILED,
                error=str(exc),
            )
        finally:
            stop_heartbeat.set()

        return _finish(
            job,
            token,
            status=FrameAnnotationExportJob.Status.COMPLETED,
            result=_job_result(result),
        )


def recover_stale_frame_annotation_export_jobs() -> int:
    """Mark jobs whose worker vanished as lost so their run name is released.

    Releasing the database row does not admit a parallel writer: a new job still
    needs the run lock, which a living worker keeps holding.
    """
    now = timezone.now()
    running = FrameAnnotationExportJob.objects.filter(
        status=FrameAnnotationExportJob.Status.RUNNING,
        heartbeat_at__lt=now - FRAME_ANNOTATION_EXPORT_RUNNING_STALE_AFTER,
    ).update(
        status=FrameAnnotationExportJob.Status.LOST,
        error="Worker stopped sending heartbeats; start a new export.",
        finished_at=now,
    )
    queued = FrameAnnotationExportJob.objects.filter(
        status=FrameAnnotationExportJob.Status.QUEUED,
        created_at__lt=now - FRAME_ANNOTATION_EXPORT_QUEUED_STALE_AFTER,
    ).update(
        status=FrameAnnotationExportJob.Status.LOST,
        error="No worker started the export; start a new export.",
        finished_at=now,
    )
    if running or queued:
        logger.warning(
            "Marked frame annotation export jobs lost: running=%s queued=%s",
            running,
            queued,
        )
    return running + queued


@dataclass(frozen=True, slots=True)
class FrameAnnotationExportScopeOptions:
    """Scopes the user may choose; mirrors the checks in ``_resolve_scope``."""

    centers: tuple[Center, ...]
    all_centers_allowed: bool


def frame_annotation_export_scope_options(
    user: ExportUser,
) -> FrameAnnotationExportScopeOptions:
    allowed_center_ids = resolve_allowed_center_ids(user)
    centers = Center.objects.order_by("name")
    if allowed_center_ids is not None:
        centers = centers.filter(pk__in=allowed_center_ids)
    return FrameAnnotationExportScopeOptions(
        centers=tuple(centers), all_centers_allowed=allowed_center_ids is None
    )


def visible_frame_annotation_export_jobs(
    user: ExportUser,
) -> QuerySet[FrameAnnotationExportJob]:
    """Jobs within the user's center scope; all-center jobs only for admins."""
    jobs = FrameAnnotationExportJob.objects.select_related("center")
    allowed_center_ids = resolve_allowed_center_ids(user)
    if allowed_center_ids is None:
        return jobs
    return jobs.filter(center_id__in=allowed_center_ids)


def frame_annotation_export_root() -> Path:
    return ensure_within_storage_root(get_runtime_paths().frame_annotation_export)


def run_lock_path(run_name: str) -> Path:
    _single_segment("output_dir", run_name)
    lock_dir = frame_annotation_export_root() / FRAME_ANNOTATION_EXPORT_LOCK_DIR_NAME
    lock_path = lock_dir / f"{run_name}.lock"
    if lock_dir.resolve() != lock_dir or lock_path.is_symlink():
        raise ValidationError("Export lock paths must not be symbolic links.")
    return lock_path


def _single_segment(field: str, value: str | None) -> str:
    if value is None or _PATH_SEGMENT.fullmatch(value) is None:
        raise ValidationError(
            f"{field} must be one name of letters, digits, '.', '_' or '-' "
            "that starts with a letter or digit."
        )
    return value


def _resolve_scope(
    config: VideoFrameAnnotationExportConfigPayload, *, user: ExportUser
) -> Center | None:
    if bool(config.center_key) == config.all_centers:
        raise ValidationError(
            "Exactly one export scope is required: center_key or all_centers."
        )
    allowed_center_ids = resolve_allowed_center_ids(user)
    if config.all_centers:
        if allowed_center_ids is not None:
            raise PermissionDenied("all_centers exports are limited to administrators.")
        return None

    center = Center.objects.filter(center_key=config.center_key).first()
    if center is None:
        raise ValidationError(f"Unknown center_key: {config.center_key}")
    if allowed_center_ids is not None and center.pk not in allowed_center_ids:
        raise PermissionDenied("center_key is outside your center scope.")
    return center


def _relative_to_export_root(path: Path) -> str:
    return path.resolve().relative_to(frame_annotation_export_root()).as_posix()


def _optional_relative(path: Path | None) -> str | None:
    return None if path is None else _relative_to_export_root(path)


def _confine_run(run_name: str, output_path: str) -> tuple[Path, Path]:
    """Confine the run and reject run names that alias another directory."""
    output_dir, relative_path = confine_export_location(run_name, output_path)
    if output_dir != frame_annotation_export_root() / run_name:
        raise ValidationError(
            f"output_dir {run_name} resolves to another directory; choose a new name."
        )
    if (output_dir / relative_path).resolve().parent != output_dir:
        raise ValidationError("Export table must stay within its own run directory.")
    return output_dir, relative_path


def _export_config(job: FrameAnnotationExportJob) -> export_config:
    """Revalidate the stored job against current access and paths."""
    prepared = prepare_frame_annotation_export(job.config, user=job.requested_by)
    if prepared.center != job.center or prepared.run_name != job.output_dir:
        raise PermissionDenied("The export scope changed since the job was queued.")
    output_dir, output_path = _confine_run(job.output_dir, prepared.payload.output_path)
    return replace(
        export_config.from_payload(prepared.payload),
        output_dir=output_dir,
        output_path=output_path,
    )


def _job_result(result: export_result) -> FrameAnnotationExportJobResult:
    return FrameAnnotationExportJobResult(
        output_path=_relative_to_export_root(result.output_path),
        row_count=result.row_count,
        exported_video_count=result.exported_video_count,
        exported_frame_count=result.exported_frame_count,
        video_output_dir=_optional_relative(result.video_output_dir),
        frame_output_dir=_optional_relative(result.frame_output_dir),
    )


def _run_lock_busy(job: FrameAnnotationExportJob) -> FrameAnnotationExportJob:
    if job.status == FrameAnnotationExportJob.Status.QUEUED.value:
        updated = FrameAnnotationExportJob.objects.filter(
            pk=job.pk, status=FrameAnnotationExportJob.Status.QUEUED
        ).update(
            status=FrameAnnotationExportJob.Status.FAILED,
            error=(
                "A previous export still writes this run directory; "
                "start the export under a new run name."
            ),
            finished_at=timezone.now(),
        )
        if updated:
            logger.warning(
                "Frame annotation export job %s refused: run %s still locked",
                job.pk,
                job.output_dir,
            )
    # Otherwise a duplicate delivery found the owning worker alive.
    job.refresh_from_db()
    return job


def _claim(job: FrameAnnotationExportJob) -> UUID | None:
    """Claim a queued job under the held run lock and return its fencing token."""
    token = uuid.uuid4()
    now = timezone.now()
    with transaction.atomic():
        current = FrameAnnotationExportJob.objects.select_for_update().get(pk=job.pk)
        if current.status == FrameAnnotationExportJob.Status.QUEUED.value:
            FrameAnnotationExportJob.objects.filter(pk=job.pk).update(
                status=FrameAnnotationExportJob.Status.RUNNING,
                claim_token=token,
                started_at=now,
                heartbeat_at=now,
                updated_at=now,
            )
            return token
        if current.status == FrameAnnotationExportJob.Status.RUNNING.value:
            # The run lock was free, so the worker that claimed this job died;
            # its partial output is not trustworthy.
            FrameAnnotationExportJob.objects.filter(pk=job.pk).update(
                status=FrameAnnotationExportJob.Status.LOST,
                error="Worker was lost during the export; start a new export.",
                finished_at=now,
                updated_at=now,
            )
    return None


def _finish(
    job: FrameAnnotationExportJob,
    token: UUID,
    *,
    status: FrameAnnotationExportJob.Status,
    result: FrameAnnotationExportJobResult | None = None,
    error: str = "",
) -> FrameAnnotationExportJob:
    now = timezone.now()
    updated = FrameAnnotationExportJob.objects.filter(
        pk=job.pk, claim_token=token, status=FrameAnnotationExportJob.Status.RUNNING
    ).update(
        status=status,
        result=dump_frame_annotation_export_job_result(result),
        error=error,
        finished_at=now,
        updated_at=now,
    )
    if not updated:
        logger.warning(
            "Discarded %s outcome of frame annotation export job %s: claim superseded",
            status,
            job.pk,
        )
    job.refresh_from_db()
    return job


def _start_heartbeat(job_id: UUID, token: UUID) -> threading.Event:
    stop = threading.Event()

    def beat() -> None:
        try:
            while not stop.wait(
                FRAME_ANNOTATION_EXPORT_HEARTBEAT_INTERVAL.total_seconds()
            ):
                FrameAnnotationExportJob.objects.filter(
                    pk=job_id,
                    claim_token=token,
                    status=FrameAnnotationExportJob.Status.RUNNING,
                ).update(heartbeat_at=timezone.now())
        finally:
            connection.close()

    threading.Thread(
        target=beat, name=f"frame-export-heartbeat-{job_id}", daemon=True
    ).start()
    return stop


def _dispatch(job: FrameAnnotationExportJob) -> None:
    from endoreg_db.tasks import run_frame_annotation_export_task

    queue = queue_for_job_kind(HeavyJobKind.FRAME_ANNOTATION_EXPORT)
    try:
        ensure_secure_transport_for_job_kind(HeavyJobKind.FRAME_ANNOTATION_EXPORT)
        async_result = run_frame_annotation_export_task.apply_async(
            args=(str(job.pk),), queue=queue, routing_key=queue
        )
    except (RuntimeError, BrokerOperationalError, OSError) as exc:
        # A job no worker will ever see must not stay queued.
        logger.exception("Dispatch of frame annotation export job %s failed", job.pk)
        FrameAnnotationExportJob.objects.filter(
            pk=job.pk, status=FrameAnnotationExportJob.Status.QUEUED
        ).update(
            status=FrameAnnotationExportJob.Status.FAILED,
            error=f"Export could not be queued: {exc}",
            finished_at=timezone.now(),
        )
        return
    FrameAnnotationExportJob.objects.filter(pk=job.pk).update(
        celery_task_id=str(async_result.id)
    )
