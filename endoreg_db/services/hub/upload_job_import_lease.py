from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction

from endoreg_db.services.imports.lease import (
    ImportLeaseHeartbeat,
    database_now,
    owns_live_lease,
)

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.upload_job_state_machine import (
    validate_upload_job_interrupted_retry,
)
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)
DEFAULT_VIDEO_IMPORT_LEASE_SECONDS = 300
MINIMUM_VIDEO_IMPORT_LEASE_SECONDS = 30


class UploadJobImportLeaseBusy(RuntimeError):
    """Raised when another live worker owns an upload import."""

    retry_after_seconds: int

    def __init__(self, message: str, *, retry_after_seconds: int = 30) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


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
    return database_now(UploadJob.objects.filter(pk=upload_job_id))


def _locked_job(upload_job_id: str) -> UploadJob:
    return UploadJob.objects.select_for_update(of=("self",)).get(pk=upload_job_id)


def acquire_upload_job_import_lease(
    *,
    upload_job_id: str,
    owner: str,
    reservation_owner: str | None = None,
) -> UploadJobImportLease:
    normalized_owner = owner.strip()
    if not normalized_owner:
        raise ValueError("Import lease owner must not be empty")
    if reservation_owner is not None and (
        not reservation_owner.strip() or reservation_owner == normalized_owner
    ):
        raise ValueError("Reservation and execution owners must be distinct")

    with transaction.atomic():
        job = _locked_job(upload_job_id)
        from endoreg_db.services.hub.upload_job_cancellation import (
            raise_if_upload_job_cancellation_requested,
        )

        raise_if_upload_job_cancellation_requested(job)
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
        # A live lease is not reacquired, even by a redelivery with the same
        # task ID. Only the first execution may consume a queued reservation;
        # subsequent renewal uses heartbeat_upload_job_import_lease instead.
        claims_reservation = (
            reservation_owner is not None
            and job.processing_lease_owner == reservation_owner
        )
        if has_live_owner and not claims_reservation:
            emit_structured_event(
                logger,
                "video_import.lease_busy",
                level=logging.WARNING,
                upload_job_id=str(job.pk),
                fencing_epoch=int(job.processing_fencing_token),
            )
            raise UploadJobImportLeaseBusy(
                f"Upload job {job.pk} has another active import owner",
                retry_after_seconds=max(
                    10, min(60, int(_lease_duration().total_seconds() / 3))
                ),
            )

        if job.processing_lease_owner and not has_live_owner:
            validate_upload_job_interrupted_retry(current_status=job.status)

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
    if not owns_live_lease(
        current_owner=job.processing_lease_owner,
        expected_owner=lease.owner,
        current_token=int(job.processing_fencing_token),
        expected_token=lease.fencing_epoch,
        expires_at=job.processing_lease_expires_at,
        now=database_now,
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
        from endoreg_db.services.hub.upload_job_cancellation import (
            raise_if_upload_job_cancellation_requested,
        )

        raise_if_upload_job_cancellation_requested(job)
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


class UploadJobImportLeaseHeartbeat(ImportLeaseHeartbeat):
    """Upload-job renewal retains its cancellation and ownership checks."""

    def __init__(self, lease: UploadJobImportLease) -> None:
        self._lease = lease
        super().__init__(
            renew=self._renew_lease,
            lost_error=UploadJobImportLeaseLost,
            name=f"upload-import-heartbeat-{lease.upload_job_id}",
            interval_seconds=max(
                10.0, min(60.0, _lease_duration().total_seconds() / 3)
            ),
            on_failure=self._record_failure,
        )

    @property
    def lease(self) -> UploadJobImportLease:
        return self._lease

    def _renew_lease(self) -> None:
        self._lease = heartbeat_upload_job_import_lease(self._lease)

    def guard(self) -> None:
        super().guard()
        with locked_upload_job_import_lease(self._lease):
            pass

    @contextmanager
    def mutation_guard(self) -> Generator[None, None, None]:
        super().guard()
        with locked_upload_job_import_lease(self._lease):
            yield

    def _record_failure(self, error: BaseException) -> None:
        emit_structured_event(
            logger,
            "video_import.heartbeat_failed",
            level=logging.ERROR,
            upload_job_id=self._lease.upload_job_id,
            fencing_epoch=self._lease.fencing_epoch,
            error_type=error.__class__.__name__,
        )
