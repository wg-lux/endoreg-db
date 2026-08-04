from __future__ import annotations

from django.apps import apps


def cleanup_upload_job_source(upload_job) -> bool:
    upload_job_model = type(upload_job)
    if (
        upload_job.cleanup_status != upload_job_model.CleanupStatus.ELIGIBLE
        or not upload_job.source_file_persisted
    ):
        return False

    field_file = upload_job.file
    file_name = str(getattr(field_file, "name", "") or "").strip()
    if file_name:
        storage = field_file.storage
        if storage.exists(file_name):
            field_file.delete(save=False)

    upload_job.file.name = ""
    upload_job.source_file_persisted = False
    upload_job.cleanup_status = upload_job_model.CleanupStatus.COMPLETED
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
    upload_job_model = apps.get_model("endoreg_db", "UploadJob")
    queryset = upload_job_model.objects.filter(
        cleanup_status=upload_job_model.CleanupStatus.ELIGIBLE,
        source_file_persisted=True,
    ).order_by("created_at")
    if limit is not None:
        queryset = queryset[:limit]

    cleaned = 0
    for upload_job in queryset:
        cleaned += int(cleanup_upload_job_source(upload_job))

    return cleaned
