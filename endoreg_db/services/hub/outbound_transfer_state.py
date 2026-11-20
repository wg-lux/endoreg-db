"""Locked application of native hub transfer transitions to outbound jobs.

``reduce_hub_transfer_event`` in Rust decides every outbound status change.
This module is the only Python writer of ``local_status`` and ``failure_class``:
it reloads the row under ``SELECT ... FOR UPDATE`` before deciding, so a stale
in-memory instance or a duplicate event cannot overwrite a newer state.
Callers save the returned fields together with their own timestamps.
"""

from __future__ import annotations

from django.db import transaction

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
