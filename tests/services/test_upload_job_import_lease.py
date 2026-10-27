# pyright: reportPrivateUsage=false
from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import InterfaceError, OperationalError
from django.utils import timezone

from endoreg_db.models import Center, UploadJob
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportInterrupted,
    reconcile_expired_upload_job_import_leases,
    recover_interrupted_upload_jobs,
    UploadJobCleanupInProgress,
    UploadJobImportLeaseBusy,
    UploadJobImportLeaseLost,
    UploadJobImportLease,
    UploadJobImportLeaseHeartbeat,
    acquire_upload_job_import_lease,
    heartbeat_upload_job_import_lease,
    locked_upload_job_import_lease,
    release_upload_job_import_lease,
)


@pytest.mark.django_db
@pytest.mark.parametrize("error_type", [OperationalError, InterfaceError])
def test_heartbeat_database_outage_preserves_retryable_error_and_ownership(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[OperationalError] | type[InterfaceError],
) -> None:
    center = Center.objects.create(name="heartbeat-database-outage")
    job = _upload_job(center)
    source_name = job.file.name
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="live-worker"
    )
    heartbeat = UploadJobImportLeaseHeartbeat(lease)
    outage = error_type("database connection unavailable")
    monkeypatch.setattr(heartbeat, "_failure", outage)

    with pytest.raises(error_type) as raised:
        heartbeat.guard()

    assert raised.value is outage
    job.refresh_from_db()
    assert job.processing_lease_owner == lease.owner
    assert job.processing_fencing_token == lease.fencing_epoch
    assert job.processing_lease_expires_at == lease.expires_at
    assert job.retry_count == 0
    assert job.file.name == source_name


@pytest.mark.django_db
def test_heartbeat_observed_ownership_loss_still_fences_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    center = Center.objects.create(name="heartbeat-ownership-loss")
    job = _upload_job(center)
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="old-worker"
    )
    heartbeat = UploadJobImportLeaseHeartbeat(lease)
    lost = UploadJobImportLeaseLost("lease superseded")
    monkeypatch.setattr(heartbeat, "_failure", lost)

    with pytest.raises(UploadJobImportLeaseLost) as raised:
        heartbeat.guard()

    assert raised.value.__cause__ is lost


def _upload_job(center: Center) -> UploadJob:
    return UploadJob.objects.create(
        file=SimpleUploadedFile("lease.mp4", b"video", content_type="video/mp4"),
        content_type="video/mp4",
        source_center=center,
    )


def _recoverable_import() -> tuple[UploadJob, UploadJobImportLease]:
    from endoreg_db.utils.hashs import get_file_hash
    from endoreg_db.utils.storage import ensure_local_file

    job = _upload_job(Center.objects.create(name="automatic-recovery"))
    with ensure_local_file(job.file) as source:
        job.content_hash = get_file_hash(source)
    job.save(update_fields=["content_hash"])
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="lost-worker"
    )
    UploadJob.objects.filter(pk=job.pk).update(
        status=UploadJob.Status.PROCESSING,
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    job.refresh_from_db()
    return job, lease


@pytest.mark.django_db
def test_periodic_recovery_retries_retained_import_and_keeps_old_worker_fenced() -> (
    None
):
    from unittest.mock import Mock
    from endoreg_db.services.hub.import_monitoring import (
        dispatch_due_upload_job_retries,
    )
    from endoreg_db.services.hub.ingest import _reserve_video_upload_import_handoff
    from endoreg_db.services.hub.upload_job_state_machine import mark_upload_job_error

    job, old_lease = _recoverable_import()
    dispatcher = Mock()
    result = dispatch_due_upload_job_retries(dispatcher=dispatcher, queue="pipeline")
    current = UploadJob.objects.get(pk=job.pk)
    assert current.status == UploadJob.Status.RETRYING
    assert current.retryable and current.retry_count == 1
    assert current.next_retry_at is not None
    assert 25 <= (current.next_retry_at - timezone.now()).total_seconds() <= 30
    assert result.dispatched_count == 0
    assert recover_interrupted_upload_jobs() == 0
    with pytest.raises(UploadJobImportLeaseBusy, match="scheduled retry"):
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner="early-redelivery"
        )
    with pytest.raises(UploadJobImportLeaseLost):
        with locked_upload_job_import_lease(old_lease):
            pytest.fail("old worker regained publication rights")
    with pytest.raises(UploadJobImportLeaseLost):
        mark_upload_job_error(job, "stale worker failure")

    UploadJob.objects.filter(pk=job.pk).update(next_retry_at=timezone.now())
    assert (
        dispatch_due_upload_job_retries(
            dispatcher=dispatcher, queue="pipeline"
        ).dispatched_count
        == 1
    )
    dispatcher.apply_async.assert_called_once_with(
        args=(str(job.pk),), queue="pipeline", routing_key="pipeline"
    )
    assert (
        dispatch_due_upload_job_retries(
            dispatcher=dispatcher, queue="pipeline"
        ).dispatched_count
        == 0
    )
    reserved, reservation, dispatched = _reserve_video_upload_import_handoff(
        upload_job_id=str(job.pk), queue="ffmpeg_media", task_id="recovered-task"
    )
    assert dispatched and reservation is not None
    assert reserved.processing_fencing_token == reservation.fencing_epoch
    execution = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk),
        owner="recovered-worker",
        reservation_owner=reservation.owner,
    )
    assert execution.fencing_epoch > old_lease.fencing_epoch
    with locked_upload_job_import_lease(execution) as owned:
        assert owned.retry_count == 1
        with owned.file.open("rb") as source:
            assert source.read() == b"video"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "blocker",
    [
        "cancel",
        "dismiss",
        "source",
        "persisted",
        "center",
        "hash",
        "preanonymized",
        "cleanup",
        "budget",
        "owner",
        "epoch",
    ],
)
def test_interrupted_recovery_preserves_ineligible_jobs(blocker: str) -> None:
    job, _ = _recoverable_import()
    assert reconcile_expired_upload_job_import_leases() == 1
    changes: dict[str, dict[str, object]] = {
        "cancel": {"cancellation_requested_at": timezone.now()},
        "dismiss": {"overview_dismissed_at": timezone.now()},
        "source": {"file": "upload_jobs/missing-source.mp4"},
        "persisted": {"source_file_persisted": False},
        "center": {"source_center": None},
        "hash": {"content_hash": ""},
        "preanonymized": {"storage_tier": UploadJob.StorageTier.UPLOAD_PREANONYMIZED},
        "cleanup": {"cleanup_status": UploadJob.CleanupStatus.ELIGIBLE},
        "budget": {"retry_count": job.max_retries},
        "owner": {
            "processing_lease_owner": "live-worker",
            "processing_lease_expires_at": timezone.now() + timedelta(minutes=5),
            "processing_heartbeat_at": timezone.now(),
        },
        "epoch": {"processing_fencing_token": 0},
    }
    UploadJob.objects.filter(pk=job.pk).update(**changes[blocker])
    before = UploadJob.objects.values().get(pk=job.pk)
    assert recover_interrupted_upload_jobs() == 0
    assert UploadJob.objects.values().get(pk=job.pk) == before


@pytest.mark.django_db
def test_repeated_worker_loss_exhausts_existing_retry_budget() -> None:
    job, _ = _recoverable_import()
    for count in range(1, job.max_retries + 1):
        assert reconcile_expired_upload_job_import_leases() == 1
        assert recover_interrupted_upload_jobs() == 1
        job.refresh_from_db()
        assert job.retry_count == count
        assert job.next_retry_at is not None
        assert (
            30 * 2 ** (count - 1) - 5
            <= (job.next_retry_at - timezone.now()).total_seconds()
            <= 30 * 2 ** (count - 1)
        )
        # Model the normal dispatch/claim followed by another worker loss.
        from endoreg_db.services.hub.upload_job_state_machine import (
            mark_upload_job_processing,
        )

        mark_upload_job_processing(job)
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner=f"retry-{count}"
        )
        UploadJob.objects.filter(pk=job.pk).update(
            processing_lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
    assert reconcile_expired_upload_job_import_leases() == 1
    assert recover_interrupted_upload_jobs() == 0
    job.refresh_from_db()
    assert job.status == UploadJob.Status.ERROR
    assert job.error_code == UploadJob.ErrorCode.IMPORT_INTERRUPTED
    assert job.retry_count == job.max_retries
    assert not job.retryable


@pytest.mark.django_db
def test_recovery_fences_snapshots_of_the_terminal_interruption_too() -> None:
    from endoreg_db.services.hub.upload_job_state_machine import mark_upload_job_error

    job, _ = _recoverable_import()
    assert reconcile_expired_upload_job_import_leases() == 1
    job.refresh_from_db()
    assert recover_interrupted_upload_jobs() == 1
    with pytest.raises(UploadJobImportLeaseLost):
        mark_upload_job_error(job, "late terminal callback")
    job.refresh_from_db()
    assert job.status == UploadJob.Status.RETRYING
    assert job.retry_count == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_recovery_admits_only_one_retry() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connections

    job, old_lease = _recoverable_import()
    assert reconcile_expired_upload_job_import_leases() == 1
    barrier = Barrier(2)

    def recover() -> int:
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            return recover_interrupted_upload_jobs()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover)
        second = pool.submit(recover)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert sorted(results) == [0, 1]
    job.refresh_from_db()
    assert job.retry_count == 1
    assert job.processing_fencing_token == old_lease.fencing_epoch + 1
    assert job.status == UploadJob.Status.RETRYING


@pytest.mark.django_db(transaction=True)
def test_queued_reservation_is_claimed_by_only_one_delivery() -> None:
    center = Center.objects.create(
        name="delivery-center", display_name="Delivery Center"
    )
    job = _upload_job(center)
    reserved = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="queued-task:task-id"
    )
    execution = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk),
        owner="delivery-one",
        reservation_owner="queued-task:task-id",
    )
    assert execution.fencing_epoch == reserved.fencing_epoch + 1
    for owner in ("delivery-one", "delivery-two"):
        with pytest.raises(UploadJobImportLeaseBusy):
            acquire_upload_job_import_lease(
                upload_job_id=str(job.pk),
                owner=owner,
                reservation_owner="queued-task:task-id",
            )
    with pytest.raises(UploadJobImportLeaseBusy):
        acquire_upload_job_import_lease(upload_job_id=str(job.pk), owner="delivery-one")
    with pytest.raises(UploadJobImportLeaseLost):
        heartbeat_upload_job_import_lease(reserved)
    heartbeat_upload_job_import_lease(execution)

    UploadJob.objects.filter(pk=job.pk).update(
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    recovered = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk),
        owner="delivery-three",
        reservation_owner="queued-task:task-id",
    )
    assert recovered.fencing_epoch == execution.fencing_epoch + 1
    with pytest.raises(UploadJobImportLeaseLost):
        heartbeat_upload_job_import_lease(execution)


@pytest.mark.django_db(transaction=True)
def test_legacy_task_owner_is_not_treated_as_a_queued_reservation() -> None:
    center = Center.objects.create(name="legacy-owner", display_name="Legacy Owner")
    job = _upload_job(center)
    legacy = acquire_upload_job_import_lease(upload_job_id=str(job.pk), owner="task-id")
    with pytest.raises(UploadJobImportLeaseBusy):
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk),
            owner="new-delivery",
            reservation_owner="queued-task:task-id",
        )
    heartbeat_upload_job_import_lease(legacy)


@pytest.mark.django_db(transaction=True)
def test_import_lease_fences_expired_owner() -> None:
    center = Center.objects.create(name="lease-center", display_name="Lease Center")
    job = _upload_job(center)

    first = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk),
        owner="worker-one",
    )
    with pytest.raises(UploadJobImportLeaseBusy):
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk),
            owner="worker-two",
        )

    UploadJob.objects.filter(pk=job.pk).update(
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    second = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk),
        owner="worker-two",
    )

    assert second.fencing_epoch == first.fencing_epoch + 1
    with pytest.raises(UploadJobImportLeaseLost):
        heartbeat_upload_job_import_lease(first)

    with locked_upload_job_import_lease(second) as owned_job:
        owned_job.source_system = "owned"
        owned_job.save(update_fields=["source_system", "updated_at"])

    release_upload_job_import_lease(second)
    job.refresh_from_db()
    assert job.processing_lease_owner == ""
    assert job.processing_lease_expires_at is None
    assert job.processing_heartbeat_at is None
    assert job.source_system == "owned"


@pytest.mark.django_db(transaction=True)
def test_import_lease_is_blocked_by_durable_cleanup_receipt() -> None:
    center = Center.objects.create(
        name="cleanup-lease-center",
        display_name="Cleanup Lease Center",
    )
    job = _upload_job(center)
    UploadJob.objects.filter(pk=job.pk).update(
        cleanup_status=UploadJob.CleanupStatus.DELETING,
        cleanup_receipt_id="11111111-1111-1111-1111-111111111111",
        cleanup_started_at=timezone.now(),
        cleanup_fencing_token=0,
        cleanup_source_name_sha256="a" * 64,
        cleanup_source_size_bytes=5,
        cleanup_source_content_sha256="b" * 64,
    )

    with pytest.raises(UploadJobCleanupInProgress):
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk),
            owner="late-retry-worker",
        )

    job.refresh_from_db()
    with pytest.raises(RuntimeError, match="cleanup exclusively owns"):
        job.schedule_retry(
            "late retry",
            error_code=UploadJob.ErrorCode.DISPATCH_UNAVAILABLE,
            delay_seconds=30,
        )


@pytest.mark.django_db
@pytest.mark.parametrize("cancel_requested", [False, True])
def test_expired_attempt_is_fenced_and_retained(
    cancel_requested: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from endoreg_db.services.hub.upload_job_cancellation import (
        finalize_upload_job_cancellation,
    )

    job = _upload_job(Center.objects.create(name="interrupted-import"))
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="lost-worker"
    )
    requested_at = timezone.now() if cancel_requested else None
    UploadJob.objects.filter(pk=job.pk).update(
        status=UploadJob.Status.CANCEL_REQUESTED
        if cancel_requested
        else UploadJob.Status.PROCESSING,
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1),
        cancellation_requested_at=requested_at,
    )
    source_name = job.file.name
    assert reconcile_expired_upload_job_import_leases() == 1
    job.refresh_from_db()
    assert job.status == UploadJob.Status.ERROR
    assert job.error_code == UploadJob.ErrorCode.IMPORT_INTERRUPTED
    assert job.processing_fencing_token == lease.fencing_epoch + 1
    assert not job.processing_lease_owner
    assert job.processing_lease_expires_at is None
    assert job.cancellation_requested_at == requested_at
    assert job.cleanup_status == UploadJob.CleanupStatus.SKIPPED
    assert not job.retryable and job.next_retry_at is None
    assert job.retry_count == 0
    assert job.file.name == source_name
    with job.file.open("rb") as source:
        assert source.read() == b"video"
    assert finalize_upload_job_cancellation(lease) is False
    with pytest.raises(UploadJobImportLeaseLost):
        with locked_upload_job_import_lease(lease):
            pytest.fail("old owner reached publication")
    with pytest.raises(UploadJobImportInterrupted):
        acquire_upload_job_import_lease(upload_job_id=str(job.pk), owner="redelivered")
    from endoreg_db.services.hub.ingest import process_upload_job
    from endoreg_db.tasks import run_video_upload_import_task
    from endoreg_db.services.hub.upload_job_state_machine import (
        mark_upload_job_processing,
        schedule_upload_job_retry,
        transition_reimport_upload_jobs,
        mark_upload_job_error,
    )

    for consumer in (process_upload_job, run_video_upload_import_task.run):
        with pytest.raises(UploadJobImportInterrupted):
            consumer(str(job.pk))
    with pytest.raises(UploadJobImportInterrupted):
        mark_upload_job_processing(job)
    with pytest.raises(UploadJobImportInterrupted):
        schedule_upload_job_retry(
            job,
            "retry",
            error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
            delay_seconds=1,
        )
    with pytest.raises(UploadJobImportInterrupted):
        transition_reimport_upload_jobs([job], status=UploadJob.Status.PROCESSING)
    job.error_code = ""  # A stale snapshot must not erase the committed marker.
    with pytest.raises(UploadJobImportInterrupted):
        mark_upload_job_error(job, "stale failure")
    job.refresh_from_db()
    assert job.error_code == UploadJob.ErrorCode.IMPORT_INTERRUPTED
    assert not job.retryable and job.next_retry_at is None
    from endoreg_db.services.hub import ingest
    from pathlib import Path

    source_path = Path(job.file.path)
    assert (
        ingest._reuse_watcher_upload_job(
            upload_job=job, created=False, watched_path=source_path
        )
        == job
    )
    assert source_path.exists()

    # Simulate reconciliation winning after the consumer's early snapshot.
    def stale_snapshot_guard(_job: UploadJob) -> None:
        pass

    monkeypatch.setattr(
        ingest, "raise_if_upload_job_import_interrupted", stale_snapshot_guard
    )
    with pytest.raises(UploadJobImportInterrupted):
        process_upload_job(str(job.pk))
    job.refresh_from_db()
    assert job.error_code == UploadJob.ErrorCode.IMPORT_INTERRUPTED
    assert reconcile_expired_upload_job_import_leases() == 0


@pytest.mark.django_db
def test_retry_dispatch_reconciles_only_expired_owned_attempts() -> None:
    from unittest.mock import Mock
    from endoreg_db.services.hub.import_monitoring import (
        dispatch_due_upload_job_retries,
    )

    center = Center.objects.create(name="reconcile-batch")
    jobs = [_upload_job(center) for _ in range(4)]
    for index, job in enumerate(jobs):
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner=f"worker-{index}"
        )
    UploadJob.objects.filter(pk__in=[job.pk for job in jobs]).update(
        status=UploadJob.Status.PROCESSING
    )
    UploadJob.objects.filter(pk__in=[job.pk for job in jobs[:2]]).update(
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )
    UploadJob.objects.filter(pk=jobs[3].pk).update(
        processing_lease_owner="",
        processing_lease_expires_at=None,
        processing_heartbeat_at=None,
    )
    dispatcher = Mock()
    result = dispatch_due_upload_job_retries(
        dispatcher=dispatcher, queue="test", limit=1
    )
    assert result.dispatched_count == 0
    dispatcher.apply_async.assert_not_called()
    assert (
        UploadJob.objects.filter(
            error_code=UploadJob.ErrorCode.IMPORT_INTERRUPTED
        ).count()
        == 1
    )
    assert reconcile_expired_upload_job_import_leases(limit=1) == 1
    for job in jobs[2:]:
        job.refresh_from_db()
        assert job.status == UploadJob.Status.PROCESSING


@pytest.mark.django_db
@pytest.mark.parametrize("cancel_requested", [False, True])
@pytest.mark.parametrize("native_refuses", [False, True])
def test_interruption_requires_native_authorization_before_mutation(
    monkeypatch: pytest.MonkeyPatch, cancel_requested: bool, native_refuses: bool
) -> None:
    from endoreg_db.services.runtime import lifecycle_state_machine
    from endoreg_db.utils.rust_backend import (
        OperationLifecycleEventToken,
        OperationLifecycleStateToken,
    )

    job = _upload_job(Center.objects.create(name="native-interruption-guard"))
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="expired-worker"
    )
    UploadJob.objects.filter(pk=job.pk).update(
        status=UploadJob.Status.CANCEL_REQUESTED
        if cancel_requested
        else UploadJob.Status.PROCESSING,
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1),
        cancellation_requested_at=timezone.now() if cancel_requested else None,
    )
    before = UploadJob.objects.values().get(pk=job.pk)
    native_transition = lifecycle_state_machine.transition_operation_lifecycle_native
    events: list[tuple[str, str]] = []

    def reject_interruption(
        *,
        current_state: OperationLifecycleStateToken,
        event: OperationLifecycleEventToken,
    ) -> OperationLifecycleStateToken:
        events.append((current_state, event))
        if event == "reconcile_fail":
            if native_refuses:
                raise ValueError("native interruption refused")
            return "running"
        return native_transition(current_state=current_state, event=event)

    monkeypatch.setattr(
        lifecycle_state_machine,
        "transition_operation_lifecycle_native",
        reject_interruption,
    )
    with pytest.raises(
        ValueError if native_refuses else RuntimeError,
        match="native interruption refused" if native_refuses else "unexpected state",
    ):
        reconcile_expired_upload_job_import_leases()
    assert events == [("running", "ownership_lost"), ("lost", "reconcile_fail")]
    assert UploadJob.objects.values().get(pk=job.pk) == before
    job.refresh_from_db()
    assert job.processing_fencing_token == lease.fencing_epoch
    with job.file.open("rb") as source:
        assert source.read() == b"video"
