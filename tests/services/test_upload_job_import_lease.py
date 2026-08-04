from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from endoreg_db.models import Center, UploadJob
from endoreg_db.services.hub.upload_job_import_lease import (
    UploadJobImportLeaseBusy,
    UploadJobImportLeaseLost,
    acquire_upload_job_import_lease,
    heartbeat_upload_job_import_lease,
    locked_upload_job_import_lease,
    release_upload_job_import_lease,
)


def _upload_job(center: Center) -> UploadJob:
    return UploadJob.objects.create(
        file=SimpleUploadedFile("lease.mp4", b"video", content_type="video/mp4"),
        content_type="video/mp4",
        source_center=center,
    )


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
        owned_job.error_detail = "owned"
        owned_job.save(update_fields=["error_detail", "updated_at"])

    release_upload_job_import_lease(second)
    job.refresh_from_db()
    assert job.processing_lease_owner == ""
    assert job.processing_lease_expires_at is None
    assert job.processing_heartbeat_at is None
    assert job.error_detail == "owned"
