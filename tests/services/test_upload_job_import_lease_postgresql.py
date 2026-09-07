"""Separate-process evidence for upload execution ownership on PostgreSQL."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import multiprocessing
from datetime import timedelta
from multiprocessing.connection import Connection
from multiprocessing.synchronize import Barrier
from unittest.mock import patch

import pytest
from django.db import connection, connections
from django.db.models.functions import Now
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.administration.center.center import Center
from endoreg_db.services.hub import ingest
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.state.video import VideoState
from endoreg_db.services.video_files.state import get_or_create_video_state
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLeaseBusy,
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
    locked_upload_job_import_lease,
    release_upload_job_import_lease,
)


def _competing_delivery(
    job_id: str, owner: str, barrier: Barrier, result: Connection
) -> None:
    connections.close_all()
    try:
        barrier.wait(timeout=20)
        try:
            lease = acquire_upload_job_import_lease(
                upload_job_id=job_id,
                owner=owner,
                reservation_owner="queued-task:delivery",
            )
        except UploadJobImportLeaseBusy:
            result.send("busy")
        else:
            result.send(f"acquired:{lease.fencing_epoch}")
    finally:
        connections.close_all()
        result.close()


def _delayed_worker(job_id: str, result: Connection) -> None:
    connections.close_all()
    try:
        lease = acquire_upload_job_import_lease(
            upload_job_id=job_id, owner="old-worker"
        )
        result.send("acquired")
        if not result.poll(20):
            raise TimeoutError("Parent did not signal takeover")
        assert result.recv() == "takeover"
        try:
            with locked_upload_job_import_lease(lease) as job:
                job.error_detail = "stale worker wrote"
                job.save(update_fields=["error_detail"])
        except UploadJobImportLeaseLost:
            result.send("write_fenced")
        else:
            result.send("write_allowed")
        try:
            release_upload_job_import_lease(lease)
        except UploadJobImportLeaseLost:
            result.send("release_fenced")
        else:
            result.send("release_allowed")
    finally:
        connections.close_all()
        result.close()


def _competing_insert(
    center_id: int, content_hash: str, barrier: Barrier, result: Connection
) -> None:
    connections.close_all()
    original_create = ingest._create_upload_job

    def synchronized_create(
        *,
        context: ingest._UploadJobCreateContext,
        reingest_provenance_updates: JsonObject,
    ) -> UploadJob:
        barrier.wait(timeout=20)
        return original_create(
            context=context, reingest_provenance_updates=reingest_provenance_updates
        )

    try:
        with patch.object(ingest, "_create_upload_job", synchronized_create):
            try:
                _, created = ingest.create_or_reuse_upload_job(
                    uploaded_file=None,
                    source_center=Center.objects.get(pk=center_id),
                    content_type="video/mp4",
                    content_hash=content_hash,
                    idempotency_key="simultaneous-key",
                )
            except ingest.UploadJobIdempotencyConflict:
                result.send("conflict")
            else:
                result.send("created" if created else "reused")
    finally:
        connections.close_all()
        result.close()


def _competing_state(video_id: int, barrier: Barrier, result: Connection) -> None:
    connections.close_all()
    try:
        stale_video = VideoFile.objects.get(pk=video_id)
        assert stale_video.state is None
        barrier.wait(timeout=20)
        result.send(get_or_create_video_state(stale_video).pk)
    finally:
        connections.close_all()
        result.close()


@pytest.fixture
def postgres_job(transactional_db: None) -> UploadJob:
    if connection.vendor != "postgresql":
        pytest.skip(
            "Requires PostgreSQL row locking; SQLite is not concurrency evidence"
        )
    return UploadJob.objects.create(content_type="video/mp4")


@pytest.mark.parametrize("same_owner", [False, True])
def test_simultaneous_deliveries_have_one_execution_owner(
    postgres_job: UploadJob, same_owner: bool
) -> None:
    reservation = acquire_upload_job_import_lease(
        upload_job_id=str(postgres_job.pk), owner="queued-task:delivery"
    )
    connections.close_all()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    pipes = [context.Pipe(duplex=False) for _ in range(2)]
    processes = [
        context.Process(
            target=_competing_delivery,
            args=(
                str(postgres_job.pk),
                "same-delivery" if same_owner else f"delivery-{index}",
                barrier,
                pipes[index][1],
            ),
        )
        for index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        results: list[object] = []
        for reader, writer in pipes:
            writer.close()
            assert reader.poll(30), "Delivery process failed to report"
            results.append(reader.recv())
        assert results.count("busy") == 1
        assert results.count(f"acquired:{reservation.fencing_epoch + 1}") == 1
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        postgres_job.refresh_from_db()
        assert postgres_job.processing_fencing_token == reservation.fencing_epoch + 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        for reader, writer in pipes:
            reader.close()
            writer.close()


def test_delayed_process_cannot_write_or_release_after_takeover(
    postgres_job: UploadJob,
) -> None:
    connections.close_all()
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe()
    process = context.Process(
        target=_delayed_worker, args=(str(postgres_job.pk), child)
    )
    process.start()
    child.close()
    try:
        assert parent.poll(30)
        assert parent.recv() == "acquired"
        UploadJob.objects.filter(pk=postgres_job.pk).update(
            processing_lease_expires_at=Now() - timedelta(seconds=1)
        )
        current = acquire_upload_job_import_lease(
            upload_job_id=str(postgres_job.pk), owner="new-worker"
        )
        parent.send("takeover")
        for expected in ("write_fenced", "release_fenced"):
            assert parent.poll(30)
            assert parent.recv() == expected
        process.join(timeout=10)
        assert process.exitcode == 0
        postgres_job.refresh_from_db()
        assert postgres_job.error_detail == ""
        assert postgres_job.processing_lease_owner == current.owner
        release_upload_job_import_lease(current)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=10)
        parent.close()


@pytest.mark.parametrize("changed_content", [False, True])
def test_simultaneous_upload_insert_recovers_constraint_conflict(
    postgres_job: UploadJob, changed_content: bool
) -> None:
    center = Center.objects.create(name="Concurrent Upload Center")
    connections.close_all()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    pipes = [context.Pipe(duplex=False) for _ in range(2)]
    processes = [
        context.Process(
            target=_competing_insert,
            args=(
                center.pk,
                ("b" if changed_content and index else "a") * 64,
                barrier,
                pipes[index][1],
            ),
        )
        for index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        results: list[object] = []
        for reader, writer in pipes:
            writer.close()
            assert reader.poll(30), "Insert process failed to report"
            results.append(reader.recv())
        assert results.count("created") == 1
        assert results.count("conflict" if changed_content else "reused") == 1
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert UploadJob.objects.filter(source_center=center).count() == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        for reader, writer in pipes:
            reader.close()
            writer.close()


def test_simultaneous_state_creation_preserves_one_state(
    postgres_job: UploadJob,
) -> None:
    video = VideoFile.objects.create(
        center=Center.objects.create(name="Concurrent State Center"),
        video_hash="concurrent-state",
    )
    connections.close_all()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    pipes = [context.Pipe(duplex=False) for _ in range(2)]
    processes = [
        context.Process(
            target=_competing_state, args=(video.pk, barrier, pipes[index][1])
        )
        for index in range(2)
    ]
    try:
        for process in processes:
            process.start()
        results: list[object] = []
        for reader, writer in pipes:
            writer.close()
            assert reader.poll(30), "State process failed to report"
            results.append(reader.recv())
        assert results[0] == results[1]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        video.refresh_from_db()
        assert video.state is not None
        assert video.state.pk == results[0]
        assert VideoState.objects.count() == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        for reader, writer in pipes:
            reader.close()
            writer.close()
