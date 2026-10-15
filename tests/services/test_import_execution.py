"""Shared authority rejects invalid capabilities and rolls back owned writes."""

from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import transaction
from django.utils import timezone

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.models import Center, UploadJob
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLeaseHeartbeat,
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
)
from endoreg_db.services.imports.execution import ImportExecutionFence
from endoreg_db.services.reports.import_fencing import (
    acquire_report_import_fence,
    report_import_mutation_guard,
)


@pytest.mark.no_db
@pytest.mark.parametrize("attempt_id", ["", " ", "A" * 32, "a" * 31, "z" * 32])
def test_execution_identity_must_match_context_contract(attempt_id: str) -> None:
    with pytest.raises(ValueError, match="attempt_id"):
        ImportExecutionFence(attempt_id, lambda: None, nullcontext)


@pytest.mark.no_db
def test_mutation_requires_bound_authority() -> None:
    ctx = ImportContext(file_path=Path("source.mp4"), center_name="test")
    with pytest.raises(RuntimeError, match="requires an execution fence"):
        ctx.owned_mutation()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("owner", ["report", "upload"])
def test_domain_adapter_rolls_back_all_owned_writes(owner: str) -> None:
    center = Center.objects.create(name="before")
    if owner == "report":
        lease = acquire_report_import_fence("a" * 64)
        fence = ImportExecutionFence(
            lease.owner_id.hex,
            lambda: None,
            lambda: report_import_mutation_guard(lease),
        )
    else:
        job = UploadJob.objects.create(content_type="video/mp4", source_center=center)
        upload_lease = acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner="test"
        )
        heartbeat = UploadJobImportLeaseHeartbeat(upload_lease)
        fence = ImportExecutionFence(
            "a" * 32, heartbeat.guard, heartbeat.mutation_guard
        )
    with pytest.raises(ValueError, match="abort"):
        with fence.mutation_guard():
            assert not transaction.get_autocommit()
            Center.objects.filter(pk=center.pk).update(name="uncommitted")
            raise ValueError("abort")
    center.refresh_from_db()
    assert center.name == "before"


@pytest.mark.django_db(transaction=True)
def test_superseded_upload_cannot_enter_shared_mutation() -> None:
    center = Center.objects.create(name="original")
    job = UploadJob.objects.create(content_type="video/mp4", source_center=center)
    lease = acquire_upload_job_import_lease(upload_job_id=str(job.pk), owner="old")
    heartbeat = UploadJobImportLeaseHeartbeat(lease)
    fence = ImportExecutionFence("a" * 32, heartbeat.guard, heartbeat.mutation_guard)
    UploadJob.objects.filter(pk=job.pk).update(
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    replacement = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="new"
    )
    with pytest.raises(UploadJobImportLeaseLost):
        with fence.mutation_guard():
            Center.objects.filter(pk=center.pk).update(name="stale")
    center.refresh_from_db()
    job.refresh_from_db()
    assert center.name == "original"
    assert job.processing_lease_owner == replacement.owner


@pytest.mark.django_db(transaction=True)
def test_transfer_mutation_rolls_back_and_rejects_replaced_owner() -> None:
    from endoreg_db.models import NetworkNode, TransferJob
    from endoreg_db.services.hub.transfers import (
        _claim_transfer_operation,  # pyright: ignore[reportPrivateUsage]
        _transfer_import_mutation,  # pyright: ignore[reportPrivateUsage]
    )

    center = Center.objects.create(name="transfer-original")
    source = NetworkNode.objects.create(node_key="source", owning_center=center)
    target = NetworkNode.objects.create(node_key="target")
    job = TransferJob.objects.create(
        transfer_key="shared-transfer-guard",
        source_node=source,
        target_node=target,
        source_center=center,
        resource_kind=TransferJob.ResourceKind.VIDEO,
        resource_hash="a" * 64,
    )
    _, lease = _claim_transfer_operation(job.pk)
    with pytest.raises(ValueError, match="rollback"):
        with _transfer_import_mutation(lease):
            Center.objects.filter(pk=center.pk).update(name="uncommitted")
            raise ValueError("rollback")
    center.refresh_from_db()
    assert center.name == "transfer-original"
    TransferJob.objects.filter(pk=job.pk).update(
        operation_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    _, replacement = _claim_transfer_operation(job.pk)
    with pytest.raises(RuntimeError, match="no longer current"):
        with _transfer_import_mutation(lease):
            Center.objects.filter(pk=center.pk).update(name="stale")
    center.refresh_from_db()
    job.refresh_from_db()
    assert center.name == "transfer-original"
    assert job.operation_owner == replacement.owner_id
