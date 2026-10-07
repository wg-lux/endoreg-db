"""Shared ownership checks for explicit deletion of recorded import sources."""

# pyright: reportPrivateUsage=false
from pathlib import Path

from django.db.models import Q
from django.utils import timezone

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.services.hub.cleanup import (
    additional_source_paths,
    UploadSourceCleanupBlocker,
)
from endoreg_db.services.video_storage.generation_cleanup import _owned_path
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.services.hub.upload_job_files import (
    DISPOSABLE_ROLES,
    has_other_file_owner,
    mark_inventory_file_removed,
)


def check_original(source: Path, digest: str) -> None:
    _owned_path(source, get_runtime_paths().runtime_root)
    if UploadJobFile.objects.filter(
        path=str(source), upload_job__content_hash=digest, role__in=DISPOSABLE_ROLES
    ).exists():
        return
    if source.exists() and get_file_hash(source) != digest:
        raise ValueError("Original media source content has changed")


def recorded_upload_sources(
    jobs: list[UploadJob],
    *,
    digest: str,
    video_id: int | None = None,
    report_id: int | None = None,
) -> set[Path]:
    paths = get_runtime_paths()
    originals: set[Path] = set()
    now = timezone.now()
    for job in jobs:
        if (
            job.status
            not in {
                UploadJob.Status.ERROR,
                UploadJob.Status.ANONYMIZED,
                UploadJob.Status.CANCELLED,
            }
            or job.retryable
            or job.next_retry_at is not None
            or job.cleanup_status == UploadJob.CleanupStatus.DELETING
            or (
                job.processing_lease_expires_at is not None
                and job.processing_lease_expires_at > now
            )
        ):
            raise ValueError("Media source still has an active or retryable import")
        if job.file.name:
            source = _owned_path(Path(job.file.path), paths.ingest_uploads)
            registered_role = (
                UploadJobFile.objects.filter(upload_job=job, path=str(source))
                .values_list("role", flat=True)
                .first()
            )
            if registered_role is not None:
                if registered_role not in DISPOSABLE_ROLES or has_other_file_owner(
                    job, source
                ):
                    raise ValueError("Upload source inventory does not permit deletion")
            else:
                if (
                    UploadJob.objects.exclude(pk=job.pk)
                    .filter(file=job.file.name)
                    .exists()
                    or VideoFile.objects.exclude(pk=video_id)
                    .filter(Q(raw_file=job.file.name) | Q(processed_file=job.file.name))
                    .exists()
                    or RawPdfFile.objects.exclude(pk=report_id)
                    .filter(Q(file=job.file.name) | Q(processed_file=job.file.name))
                    .exists()
                ):
                    raise ValueError("Upload source is shared with another import")
                check_original(source, digest)
            originals.add(source)
        copies, blocker = additional_source_paths(job)
        if blocker != UploadSourceCleanupBlocker.NONE:
            raise ValueError(f"Original media copies are blocked: {blocker.value}")
        originals.update(copies)
    return originals


def complete_source_deletion(jobs: list[UploadJob]) -> None:
    now = timezone.now()
    for job in jobs:
        for entry in UploadJobFile.objects.filter(
            upload_job=job, removed_at__isnull=True
        ):
            path = Path(entry.path)
            if not path.exists() and not path.is_symlink():
                mark_inventory_file_removed(path)
        job.file.name = ""
        job.source_file_persisted = False
        job.cleanup_status = UploadJob.CleanupStatus.COMPLETED
        job.cleanup_completed_at = now
        job.save(
            update_fields=[
                "file",
                "source_file_persisted",
                "cleanup_status",
                "cleanup_completed_at",
                "updated_at",
            ]
        )
