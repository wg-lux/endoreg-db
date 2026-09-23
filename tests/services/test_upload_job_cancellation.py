from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.utils import timezone

from endoreg_db.models import Center, UploadJob
from endoreg_db.import_files.context import ImportContext
from endoreg_db.import_files.video_import_service import (
    cleanup_cancelled_import_staging,
)
from endoreg_db.tasks import run_video_upload_import_task
from endoreg_db.services.hub.ingest import create_or_reuse_upload_job
from endoreg_db.services.hub.upload_job_cancellation import (
    UploadJobImportCancelled,
    UploadJobCancellationCleanupFailed,
    finalize_upload_job_cancellation,
    request_upload_job_cancellation,
)
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLeaseHeartbeat,
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
    heartbeat_upload_job_import_lease,
)
from endoreg_db.utils.encryption.encrypted import EncryptedStorage
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.paths import EndoregPathsModel

pytestmark = pytest.mark.django_db


@pytest.fixture
def source_job(monkeypatch: pytest.MonkeyPatch) -> tuple[UploadJob, User]:
    monkeypatch.setattr(
        cast(models.FileField, UploadJob._meta.get_field("file")),
        "storage",
        EncryptedStorage(),
    )
    center = Center.objects.create(name="Cancellation center")
    user = User.objects.create_user(username="cancel-operator")
    return UploadJob.objects.create(
        source_center=center,
        content_type="video/mp4",
        source_file_persisted=True,
        file=SimpleUploadedFile("retained.mp4", b"retained import source"),
    ), user


@pytest.mark.parametrize("status", ["pending", "retrying"])
@pytest.mark.parametrize("reserved", [False, True])
def test_queued_cancellation_is_idempotent_and_redelivery_preserves_ciphertext(
    source_job: tuple[UploadJob, User], status: str, reserved: bool
) -> None:
    job, user = source_job
    if status == "retrying":
        job.schedule_retry(
            "dispatch unavailable", error_code="dispatch_unavailable", delay_seconds=30
        )
    initial_retry_count = job.retry_count
    if reserved:
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner="queued-task:delivery"
        )
    ciphertext = Path(job.file.path).read_bytes()
    assert ciphertext.startswith(b"LXENC01")
    result = request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    assert result.status == "cancelled"
    first_updated = result.updated_at
    result = request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    assert result.updated_at == first_updated
    assert run_video_upload_import_task.run(str(job.pk)) is False
    job.refresh_from_db()
    assert job.status == "cancelled"
    assert job.processing_lease_owner == ""
    assert job.retry_count == initial_retry_count
    assert Path(job.file.path).read_bytes() == ciphertext


def test_running_cancellation_requires_owner_acknowledgement_and_preserves_source(
    source_job: tuple[UploadJob, User],
) -> None:
    job, user = source_job
    lease = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="execution-running"
    )
    job.refresh_from_db()
    job.mark_processing()
    ciphertext = Path(job.file.path).read_bytes()
    result = request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    assert result.status == "cancel_requested"
    assert result.processing_lease_owner == lease.owner
    with pytest.raises(UploadJobImportCancelled):
        UploadJobImportLeaseHeartbeat(lease).guard()
    assert finalize_upload_job_cancellation(lease) is True
    job.refresh_from_db()
    assert job.status == "cancelled"
    assert job.processing_lease_owner == ""
    assert job.processing_lease_expires_at is None
    assert Path(job.file.path).read_bytes() == ciphertext


def test_stale_owner_cannot_acknowledge_or_override_cancellation(
    source_job: tuple[UploadJob, User],
) -> None:
    job, user = source_job
    stale = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="execution-stale"
    )
    UploadJob.objects.filter(pk=job.pk).update(
        processing_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    current = acquire_upload_job_import_lease(
        upload_job_id=str(job.pk), owner="execution-current"
    )
    request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    with pytest.raises(UploadJobImportLeaseLost):
        heartbeat_upload_job_import_lease(stale)
    assert finalize_upload_job_cancellation(stale) is False
    job.refresh_from_db()
    assert job.status == "cancel_requested"
    assert job.processing_lease_owner == current.owner
    assert finalize_upload_job_cancellation(current) is True


@pytest.mark.parametrize("status", ["anonymized", "error", "lost"])
def test_terminal_jobs_cannot_be_cancelled(
    source_job: tuple[UploadJob, User], status: str
) -> None:
    job, user = source_job
    UploadJob.objects.filter(pk=job.pk).update(
        status=status,
        error_code="source_missing" if status == "lost" else "processing_failed",
    )
    with pytest.raises(ValueError):
        request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    job.refresh_from_db()
    assert job.status == status


def test_missing_source_handle_cannot_accept_cancellation(
    source_job: tuple[UploadJob, User],
) -> None:
    job, user = source_job
    UploadJob.objects.filter(pk=job.pk).update(file="")
    with pytest.raises(ValueError):
        request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    job.refresh_from_db()
    assert job.status == "pending"


def test_absent_source_file_cannot_accept_cancellation(
    source_job: tuple[UploadJob, User],
) -> None:
    job, user = source_job
    field = cast(models.FileField, UploadJob._meta.get_field("file"))
    source_name = job.file.name
    assert source_name is not None
    field.storage.delete(source_name)
    with pytest.raises(ValueError):
        request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    job.refresh_from_db()
    assert job.status == "pending"


@pytest.mark.parametrize("boundary", ["preanonymized", "unowned_processing"])
def test_unfenced_import_rejects_cancellation(
    source_job: tuple[UploadJob, User], boundary: str
) -> None:
    job, user = source_job
    if boundary == "preanonymized":
        UploadJob.objects.filter(pk=job.pk).update(storage_tier="upload_preanonymized")
    else:
        job.mark_processing()
    with pytest.raises(ValueError):
        request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    job.refresh_from_db()
    assert job.status not in ("cancel_requested", "cancelled")


@pytest.mark.parametrize("running", [False, True])
def test_replayed_upload_reuses_stopped_ledger_without_restarting(
    source_job: tuple[UploadJob, User], running: bool
) -> None:
    job, user = source_job
    UploadJob.objects.filter(pk=job.pk).update(
        content_hash="a" * 64, idempotency_key="cancelled-import-key"
    )
    if running:
        acquire_upload_job_import_lease(
            upload_job_id=str(job.pk), owner="execution-replay"
        )
    stopped = request_upload_job_cancellation(job_id=str(job.pk), actor_id=user.pk)
    ciphertext = Path(job.file.path).read_bytes()
    reused, created = create_or_reuse_upload_job(
        uploaded_file=None,
        content_type="video/mp4",
        source_center=job.source_center,
        content_hash="a" * 64,
        idempotency_key="cancelled-import-key",
    )
    assert created is False
    assert reused.pk == job.pk
    assert reused.status == stopped.status
    assert Path(job.file.path).read_bytes() == ciphertext


def _staging_context() -> ImportContext:
    paths = EndoregPathsModel.from_environment()
    root = paths.transcoding / f"cancellation-{uuid4().hex}"
    original = root / "original.mp4"
    sensitive = root / "sensitive.mp4"
    anonymized = root / "partial.mp4"
    for candidate in (original, sensitive, anonymized):
        atomic_write_file(
            destination=candidate, content=[b"test import artifact"], file_mode=0o600
        )
    return ImportContext(
        file_path=original,
        original_path=original,
        sensitive_path=sensitive,
        anonymized_path=anonymized,
        center_name="Cancellation staging center",
    )


def test_interrupted_scope_removes_staging_and_preserves_original() -> None:
    ctx = _staging_context()
    interruption = UploadJobImportCancelled("operator interruption")
    with pytest.raises(UploadJobImportCancelled) as raised:
        with cleanup_cancelled_import_staging(ctx):
            raise interruption
    assert raised.value is interruption
    assert ctx.file_path.read_bytes() == b"test import artifact"
    assert ctx.sensitive_path is not None and not ctx.sensitive_path.exists()
    assert ctx.anonymized_path is not None and not ctx.anonymized_path.exists()


@pytest.mark.parametrize("overlap", ["original", "outside_root"])
def test_cleanup_refusal_preserves_original_and_canonical(
    tmp_path: Path, overlap: str
) -> None:
    ctx = _staging_context()
    canonical = atomic_write_file(
        destination=tmp_path / "canonical.mp4",
        content=[b"canonical artifact"],
        file_mode=0o600,
    )
    ctx.anonymized_path = ctx.original_path if overlap == "original" else canonical
    with pytest.raises(UploadJobCancellationCleanupFailed):
        with cleanup_cancelled_import_staging(ctx):
            raise UploadJobImportCancelled("operator interruption")
    assert ctx.file_path.read_bytes() == b"test import artifact"
    assert canonical.read_bytes() == b"canonical artifact"


def test_successful_scope_keeps_staging_for_normal_publication() -> None:
    ctx = _staging_context()
    with cleanup_cancelled_import_staging(ctx):
        pass
    assert ctx.sensitive_path is not None and ctx.sensitive_path.exists()
    assert ctx.anonymized_path is not None and ctx.anonymized_path.exists()


def test_unlink_error_prevents_successful_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _staging_context()

    def denied_unlink(path: Path, *, missing_ok: bool = True) -> None:
        raise PermissionError("test staging removal denied")

    monkeypatch.setattr(
        "endoreg_db.import_files.file_storage.cleanup.safe_unlink_file", denied_unlink
    )
    with pytest.raises(UploadJobCancellationCleanupFailed):
        with cleanup_cancelled_import_staging(ctx):
            raise UploadJobImportCancelled("operator interruption")
    assert ctx.anonymized_path is not None and ctx.anonymized_path.exists()
    assert ctx.file_path.exists()
