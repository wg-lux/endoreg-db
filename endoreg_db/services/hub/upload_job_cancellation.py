"""Persist import cancellation intent; only the owning worker acknowledges it."""

from __future__ import annotations

import logging
from typing import Protocol, cast

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.upload_job_import_lease import UploadJobImportLease
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


class _SourceStorage(Protocol):
    def exists(self, name: str) -> bool: ...


class _RetainedSourceFile(Protocol):
    name: str
    storage: _SourceStorage


class UploadJobCancellationConflict(ValueError):
    """The import cannot safely accept a cancellation intent."""


class UploadJobImportCancelled(RuntimeError):
    """The current import must unwind at its next owned checkpoint."""


class UploadJobCancellationCleanupFailed(RuntimeError):
    """Cancellation cannot be acknowledged while attempt plaintext remains."""


def raise_if_upload_job_cancellation_requested(job: UploadJob) -> None:
    if job.status in {UploadJob.Status.CANCEL_REQUESTED, UploadJob.Status.CANCELLED}:
        raise UploadJobImportCancelled("Video import cancellation requested")


def request_upload_job_cancellation(*, job_id: str, actor_id: int) -> UploadJob:
    with transaction.atomic():
        job = UploadJob.objects.select_for_update().get(pk=job_id)
        source_file = cast(_RetainedSourceFile, job.file)
        if (
            not job.source_file_persisted
            or not job.file
            or not job.file.name
            or not source_file.storage.exists(source_file.name)
        ):
            raise UploadJobCancellationConflict(
                "The retained import source is unavailable."
            )
        if job.status in {
            UploadJob.Status.CANCEL_REQUESTED,
            UploadJob.Status.CANCELLED,
        }:
            return job
        if (
            job.status
            not in {
                UploadJob.Status.PENDING,
                UploadJob.Status.PROCESSING,
                UploadJob.Status.RETRYING,
            }
            or not job.content_type.startswith("video/")
            or job.storage_tier == UploadJob.StorageTier.UPLOAD_PREANONYMIZED.value
            or (
                job.status == UploadJob.Status.PROCESSING.value
                and not job.processing_lease_owner
            )
            or not job.source_file_persisted
            or not job.file
            or job.cleanup_status
            not in {UploadJob.CleanupStatus.PENDING, UploadJob.CleanupStatus.SKIPPED}
        ):
            raise UploadJobCancellationConflict(
                "This import cannot safely be interrupted."
            )
        active_execution = bool(
            job.processing_lease_owner
        ) and not job.processing_lease_owner.startswith("queued-task:")
        job.status = (
            UploadJob.Status.CANCEL_REQUESTED
            if active_execution
            else UploadJob.Status.CANCELLED
        )
        job.cancellation_requested_at = timezone.now()
        job.cancellation_requested_by = User.objects.get(pk=actor_id)
        job.retryable = False
        job.next_retry_at = None
        job.cleanup_status = UploadJob.CleanupStatus.SKIPPED
        fields = [
            "status",
            "cancellation_requested_at",
            "cancellation_requested_by",
            "retryable",
            "next_retry_at",
            "cleanup_status",
            "updated_at",
        ]
        if not active_execution:
            job.processing_fencing_token += 1
            job.processing_lease_owner = ""
            job.processing_lease_expires_at = None
            job.processing_heartbeat_at = None
            fields += [
                "processing_fencing_token",
                "processing_lease_owner",
                "processing_lease_expires_at",
                "processing_heartbeat_at",
            ]
        job.save(update_fields=fields)
        emit_structured_event(
            logger,
            "video_import.cancellation_requested",
            upload_job_id=str(job.pk),
            actor_id=actor_id,
            status=job.status,
        )
        return job


def finalize_upload_job_cancellation(lease: UploadJobImportLease) -> bool:
    """Acknowledge after unwinding; never release another worker's ownership."""
    with transaction.atomic():
        job = UploadJob.objects.select_for_update().get(pk=lease.upload_job_id)
        if (
            job.status != UploadJob.Status.CANCEL_REQUESTED.value
            or job.processing_lease_owner != lease.owner
            or job.processing_fencing_token != lease.fencing_epoch
        ):
            return False
        job.status = UploadJob.Status.CANCELLED
        job.processing_lease_owner = ""
        job.processing_lease_expires_at = None
        job.processing_heartbeat_at = None
        job.save(
            update_fields=[
                "status",
                "processing_lease_owner",
                "processing_lease_expires_at",
                "processing_heartbeat_at",
                "updated_at",
            ]
        )
        emit_structured_event(
            logger,
            "video_import.cancelled",
            upload_job_id=str(job.pk),
            fencing_epoch=lease.fencing_epoch,
        )
        return True
