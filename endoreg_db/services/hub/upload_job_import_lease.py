from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models.functions import Now

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)
DEFAULT_VIDEO_IMPORT_LEASE_SECONDS = 300
MINIMUM_VIDEO_IMPORT_LEASE_SECONDS = 30


class UploadJobImportLeaseBusy(RuntimeError):
    """Raised when another live worker owns an upload import."""


class UploadJobImportLeaseLost(RuntimeError):
    """Raised when a worker no longer owns the current fencing epoch."""


class UploadJobCleanupInProgress(RuntimeError):
    """Raised when a durable cleanup receipt exclusively owns the source."""


@dataclass(frozen=True)
class UploadJobImportLease:
    upload_job_id: str
    owner: str
    fencing_epoch: int
    expires_at: datetime


def _lease_duration() -> timedelta:
    configured = int(
        getattr(
            settings,
            "VIDEO_IMPORT_LEASE_SECONDS",
            DEFAULT_VIDEO_IMPORT_LEASE_SECONDS,
        )
    )
    if configured < MINIMUM_VIDEO_IMPORT_LEASE_SECONDS:
        raise ValueError(
            "VIDEO_IMPORT_LEASE_SECONDS must be at least "
            f"{MINIMUM_VIDEO_IMPORT_LEASE_SECONDS}"
        )
    return timedelta(seconds=configured)


def _database_now(upload_job_id: str) -> datetime:
    value = (
        UploadJob.objects.filter(pk=upload_job_id)
        .annotate(database_now=Now())
        .values_list("database_now", flat=True)
        .get()
    )
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a typed current timestamp")
    return value


def _locked_job(upload_job_id: str) -> UploadJob:
    return UploadJob.objects.select_for_update(of=("self",)).get(pk=upload_job_id)


def acquire_upload_job_import_lease(
    *,
    upload_job_id: str,
    owner: str,
) -> UploadJobImportLease:
    normalized_owner = owner.strip()
    if not normalized_owner:
        raise ValueError("Import lease owner must not be empty")

    with transaction.atomic():
        job = _locked_job(upload_job_id)
        if job.cleanup_status == UploadJob.CleanupStatus.DELETING.value:
            emit_structured_event(
                logger,
                "video_import.lease_cleanup_blocked",
                level=logging.WARNING,
                upload_job_id=str(job.pk),
                fencing_epoch=int(job.processing_fencing_token),
            )
            raise UploadJobCleanupInProgress(
                f"Upload job {job.pk} source cleanup is in progress"
            )
        database_now = _database_now(upload_job_id)
        current_expiry = job.processing_lease_expires_at
        has_live_owner = (
            bool(job.processing_lease_owner)
            and current_expiry is not None
            and current_expiry > database_now
        )
        if has_live_owner and job.processing_lease_owner != normalized_owner:
            emit_structured_event(
                logger,
                "video_import.lease_busy",
                level=logging.WARNING,
                upload_job_id=str(job.pk),
                fencing_epoch=int(job.processing_fencing_token),
            )
            raise UploadJobImportLeaseBusy(
                f"Upload job {job.pk} has another active import owner"
            )

        if job.processing_lease_owner != normalized_owner or not has_live_owner:
            job.processing_fencing_token += 1

        expires_at = database_now + _lease_duration()
        job.processing_lease_owner = normalized_owner
        job.processing_lease_expires_at = expires_at
        job.processing_heartbeat_at = database_now
        job.save(
            update_fields=[
                "processing_lease_owner",
                "processing_lease_expires_at",
                "processing_heartbeat_at",
                "processing_fencing_token",
                "updated_at",
            ]
        )
        lease = UploadJobImportLease(
            upload_job_id=str(job.pk),
            owner=normalized_owner,
            fencing_epoch=int(job.processing_fencing_token),
            expires_at=expires_at,
        )

    emit_structured_event(
        logger,
        "video_import.lease_acquired",
        upload_job_id=lease.upload_job_id,
        fencing_epoch=lease.fencing_epoch,
        lease_seconds=int(_lease_duration().total_seconds()),
    )
    return lease


def _verify_locked_lease(
    job: UploadJob,
    lease: UploadJobImportLease,
    *,
    database_now: datetime,
) -> None:
    if (
        job.processing_lease_owner != lease.owner
        or int(job.processing_fencing_token) != lease.fencing_epoch
        or job.processing_lease_expires_at is None
        or job.processing_lease_expires_at <= database_now
    ):
        emit_structured_event(
            logger,
            "video_import.fencing_rejected",
            level=logging.ERROR,
            upload_job_id=str(job.pk),
            attempted_fencing_epoch=lease.fencing_epoch,
            current_fencing_epoch=int(job.processing_fencing_token),
        )
        raise UploadJobImportLeaseLost(
            f"Upload job {job.pk} import lease is expired or fenced"
        )


def heartbeat_upload_job_import_lease(
    lease: UploadJobImportLease,
) -> UploadJobImportLease:
    with transaction.atomic():
        job = _locked_job(lease.upload_job_id)
        database_now = _database_now(lease.upload_job_id)
        _verify_locked_lease(job, lease, database_now=database_now)
        expires_at = database_now + _lease_duration()
        job.processing_heartbeat_at = database_now
        job.processing_lease_expires_at = expires_at
        job.save(
            update_fields=[
                "processing_heartbeat_at",
                "processing_lease_expires_at",
                "updated_at",
            ]
        )
    return UploadJobImportLease(
        upload_job_id=lease.upload_job_id,
        owner=lease.owner,
        fencing_epoch=lease.fencing_epoch,
        expires_at=expires_at,
    )


@contextmanager
def locked_upload_job_import_lease(
    lease: UploadJobImportLease,
) -> Generator[UploadJob, None, None]:
    with transaction.atomic():
        job = _locked_job(lease.upload_job_id)
        _verify_locked_lease(
            job,
            lease,
            database_now=_database_now(lease.upload_job_id),
        )
        yield job


def release_upload_job_import_lease(lease: UploadJobImportLease) -> None:
    with locked_upload_job_import_lease(lease) as job:
        job.processing_lease_owner = ""
        job.processing_lease_expires_at = None
        job.processing_heartbeat_at = None
        job.save(
            update_fields=[
                "processing_lease_owner",
                "processing_lease_expires_at",
                "processing_heartbeat_at",
                "updated_at",
            ]
        )
    emit_structured_event(
        logger,
        "video_import.lease_released",
        upload_job_id=lease.upload_job_id,
        fencing_epoch=lease.fencing_epoch,
    )


class UploadJobImportLeaseHeartbeat:
    """Renews a lease and surfaces background renewal failure at phase guards."""

    def __init__(self, lease: UploadJobImportLease) -> None:
        self._lease = lease
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        interval = max(
            10.0,
            min(60.0, _lease_duration().total_seconds() / 3),
        )
        self._interval_seconds = interval
        self._thread = threading.Thread(
            target=self._run,
            name=f"video-import-heartbeat-{lease.upload_job_id}",
            daemon=True,
        )

    @property
    def lease(self) -> UploadJobImportLease:
        return self._lease

    def __enter__(self) -> "UploadJobImportLeaseHeartbeat":
        self._thread.start()
        return self

    def guard(self) -> None:
        if self._failure is not None:
            raise UploadJobImportLeaseLost(
                f"Upload import heartbeat failed: {self._failure}"
            ) from self._failure
        self._lease = heartbeat_upload_job_import_lease(self._lease)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=min(5.0, self._interval_seconds))
        close_old_connections()

    def _run(self) -> None:
        close_old_connections()
        try:
            while not self._stop.wait(self._interval_seconds):
                self._lease = heartbeat_upload_job_import_lease(self._lease)
        except BaseException as exc:
            self._failure = exc
            emit_structured_event(
                logger,
                "video_import.heartbeat_failed",
                level=logging.ERROR,
                upload_job_id=self._lease.upload_job_id,
                fencing_epoch=self._lease.fencing_epoch,
                error_type=exc.__class__.__name__,
            )
        finally:
            close_old_connections()
