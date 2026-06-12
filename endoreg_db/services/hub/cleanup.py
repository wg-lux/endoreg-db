from __future__ import annotations

from typing import Protocol, cast

from django.core.files.storage import Storage
from django.db.models.fields.files import FieldFile

from endoreg_db.models.hub.upload_job import UploadJob


class _StoredFileLike(Protocol):
    name: str
    storage: Storage


def cleanup_upload_job_source(upload_job: UploadJob) -> bool:
    if (
        upload_job.cleanup_status != UploadJob.CleanupStatus.ELIGIBLE.value
        or not upload_job.source_file_persisted
    ):
        return False

    field_file: FieldFile = upload_job.file
    file_name = (field_file.name or "").strip()
    stored_file = cast(_StoredFileLike, field_file)
    if file_name and stored_file.storage.exists(file_name):
        field_file.delete(save=False)

    upload_job.file.name = ""
    upload_job.source_file_persisted = False
    upload_job.cleanup_status = UploadJob.CleanupStatus.COMPLETED.value
    upload_job.save(
        update_fields=[
            "file",
            "source_file_persisted",
            "cleanup_status",
            "updated_at",
        ]
    )
    return True


def reap_upload_job_sources(*, limit: int | None = None) -> int:
    queryset = UploadJob.objects.filter(
        cleanup_status=UploadJob.CleanupStatus.ELIGIBLE.value,
        source_file_persisted=True,
    ).order_by("created_at")
    if limit is not None:
        queryset = queryset[:limit]

    cleaned = 0
    for upload_job in queryset:
        cleaned += int(cleanup_upload_job_source(upload_job))

    return cleaned
