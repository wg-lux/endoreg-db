"""Locked application of native hub transfer transitions to outbound jobs.

``reduce_hub_transfer_event`` in Rust decides every outbound status change.
This module is the only Python writer of ``local_status`` and ``failure_class``:
it reloads the row under ``SELECT ... FOR UPDATE`` before deciding, so a stale
in-memory instance or a duplicate event cannot overwrite a newer state.
Callers save the returned fields together with their own timestamps.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from endoreg_db.models import RawPdfFile, VideoFile

from endoreg_db.models.hub.outbound_hub_transfer_job import OutboundHubTransferJob
from endoreg_db.models.state.hub_transfer import HubExportFailureClass
from endoreg_db.utils.rust_backend import HubTransferEvent, reduce_hub_transfer_event

TRANSITION_FIELDS = ("local_status", "failure_class")


def lock_outbound_job(job: OutboundHubTransferJob) -> None:
    """Reload ``job`` under its row lock inside the caller's transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Outbound hub transfer transitions require an atomic block")
    job.refresh_from_db(
        from_queryset=OutboundHubTransferJob.objects.select_for_update()
    )


def apply_outbound_transfer_event(
    job: OutboundHubTransferJob,
    event: HubTransferEvent,
    *,
    remote_status: str | None = None,
    failure_class: HubExportFailureClass | None = None,
) -> bool:
    """Lock, decide natively and assign the status pair; True means the caller saves.

    ``job`` is reloaded first, so unsaved changes on it are discarded.
    """
    lock_outbound_job(job)
    status, failure, apply = reduce_hub_transfer_event(
        current_state=job.local_status,
        current_failure_class=job.failure_class or None,
        event=event,
        remote_status=remote_status,
        failure_class=failure_class,
    )
    if apply:
        job.local_status = status
        job.failure_class = failure or ""
    return apply


def other_generation_blocks_dispatch(job: OutboundHubTransferJob) -> bool:
    """Serialize generations on their resource; unresolved sends retain priority."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Generation dispatch requires an atomic transaction.")
    if job.video_file_id is not None:
        VideoFile.objects.select_for_update().get(pk=job.video_file_id)
        others = OutboundHubTransferJob.objects.filter(video_file_id=job.video_file_id)
    elif job.raw_pdf_file_id is not None:
        RawPdfFile.objects.select_for_update().get(pk=job.raw_pdf_file_id)
        others = OutboundHubTransferJob.objects.filter(
            raw_pdf_file_id=job.raw_pdf_file_id
        )
    else:
        raise ValueError("Outbound transfer has no clinical resource.")
    return (
        others.filter(target_node_id=job.target_node_id)
        .exclude(pk=job.pk)
        .exclude(
            local_status=job.LocalStatus.COMPLETED,
        )
        .filter(
            Q(registration_started_at__isnull=False)
            | Q(
                local_status__in=[
                    job.LocalStatus.QUEUED,
                    job.LocalStatus.REGISTERING,
                    job.LocalStatus.AWAITING_MEDIA,
                    job.LocalStatus.UPLOADING,
                ]
            )
        )
        .exists()
    )
