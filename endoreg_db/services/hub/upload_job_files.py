"""Central ownership inventory shared by import staging and source cleanup."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.models.media.pdf.raw_pdf import RawPdfFile
from endoreg_db.models.media.frame.frame import Frame
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.schemas.hub_payloads import UploadProvenancePayload
from endoreg_db.schemas.processed_video_cleanup import cleanup_receipts
from endoreg_db.utils.file_inventory import file_inventory_scope
from endoreg_db.utils.paths import get_runtime_paths

LEGACY_FILE_FIELDS = {
    "watched_path": UploadJobFile.Role.SOURCE,
    "watcher_processing_path": UploadJobFile.Role.SOURCE,
    "stored_upload_path": UploadJobFile.Role.SOURCE,
    "legacy_source_path": UploadJobFile.Role.SOURCE,
    "migrated_destination_path": UploadJobFile.Role.SOURCE,
    "sidecar_path": UploadJobFile.Role.SIDECAR,
    "quarantined_path": UploadJobFile.Role.QUARANTINE,
    "quarantined_sidecar_path": UploadJobFile.Role.QUARANTINE,
}
DISPOSABLE_ROLES = (
    UploadJobFile.Role.SOURCE,
    UploadJobFile.Role.SIDECAR,
    UploadJobFile.Role.WORKING,
)
_current_job: ContextVar[UUID | None] = ContextVar(
    "file_inventory_upload_job", default=None
)


@dataclass(frozen=True)
class UploadFileReference:
    path: Path
    role: UploadJobFile.Role
    sha256: str
    registered: bool = False


def inventory_path(path: Path | str) -> Path:
    """Normalize aliases without resolving away evidence of symbolic links."""
    target = Path(path)
    if not target.is_absolute():
        target = get_runtime_paths().storage / target
    if ".." in target.parts:
        raise ValueError("Upload file inventory rejects parent traversal")
    return target


def _default_role(path: Path) -> UploadJobFile.Role:
    paths = get_runtime_paths()
    if path.is_relative_to(paths.quarantine):
        return UploadJobFile.Role.QUARANTINE
    if path.is_relative_to(paths.transcoding):
        return UploadJobFile.Role.WORKING
    if any(
        path.is_relative_to(root)
        for root in (
            paths.ingest_uploads,
            paths.import_video,
            paths.import_report,
            paths.import_preanonymized,
            paths.import_anonymized_video,
            paths.import_anonymized_report,
        )
    ):
        return UploadJobFile.Role.SOURCE
    # Canonical raw/processed media share roots with some staging files.
    # Only explicit staging registration may make those paths disposable.
    return UploadJobFile.Role.RETAINED


def register_upload_job_file(
    upload_job_id: UUID,
    path: Path,
    *,
    role: UploadJobFile.Role | None = None,
    planned: bool = False,
    is_directory: bool = False,
) -> None:
    target = inventory_path(path)
    if target.is_symlink() or target.resolve() != target:
        raise ValueError("Upload file inventory rejects symbolic links")
    if target.is_dir() and not is_directory:
        raise ValueError("Register individual files, not shared directories")
    with transaction.atomic():
        job = UploadJob.objects.select_for_update().get(pk=upload_job_id)
        if job.cleanup_status == UploadJob.CleanupStatus.DELETING:
            raise RuntimeError("Upload source cleanup owns the file inventory")
        entry, created = UploadJobFile.objects.get_or_create(
            upload_job=job,
            path=str(target),
            defaults={
                "role": role or _default_role(target),
                "is_directory": is_directory,
            },
        )
        if role is not None:
            entry.role = role
        if not planned and target.exists():
            entry.size_bytes = target.stat().st_size
            entry.removed_at = None
        elif not planned and not created:
            entry.removed_at = timezone.now()
        entry.save()


def record_working_file(path: Path) -> None:
    """Register an explicit attempt-owned artifact, including planned outputs."""
    job_id = _current_job.get()
    if job_id is not None:
        register_upload_job_file(job_id, path, role=UploadJobFile.Role.WORKING)


@contextmanager
def track_working_directory(directory: Path) -> Generator[None]:
    """Declare an exclusive external-tool workspace before it creates any files."""
    job_id = _current_job.get()
    if job_id is None:
        yield
        return
    paths = get_runtime_paths()
    if not any(
        directory != root and directory.is_relative_to(root)
        for root in (
            paths.transcoding,
            paths.import_anonymized_report,
            paths.import_anonymized_video,
        )
    ):
        raise ValueError(
            "Working directory must be an exclusive protected staging directory"
        )
    register_upload_job_file(
        job_id, directory, role=UploadJobFile.Role.WORKING, is_directory=True
    )
    try:
        yield
    finally:
        for child in directory.rglob("*"):
            if child.is_symlink():
                raise ValueError("Symbolic link in working directory")
            if not child.is_dir():
                register_upload_job_file(job_id, child, role=UploadJobFile.Role.WORKING)


def mark_inventory_file_removed(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("Cannot record deletion while file remains")
    UploadJobFile.objects.filter(
        path=str(inventory_path(path)), removed_at__isnull=True
    ).update(removed_at=timezone.now())


def upload_job_file_inventory(job: UploadJob) -> tuple[UploadFileReference, ...]:
    """Read persisted ownership plus exact legacy references, without scanning roots."""
    provenance = UploadProvenancePayload.model_validate(job.processing_provenance)
    files: dict[Path, UploadFileReference] = {}
    if job.file.name:
        path = inventory_path(job.file.path)
        files[path] = UploadFileReference(
            path, UploadJobFile.Role.SOURCE, job.content_hash
        )
    for field, role in LEGACY_FILE_FIELDS.items():
        name = getattr(provenance, field)
        if isinstance(name, str) and name:
            path = inventory_path(name)
            files[path] = UploadFileReference(
                path,
                role,
                job.content_hash if role == UploadJobFile.Role.SOURCE else "",
            )
    # The persisted inventory is authoritative, not a new hash-discovery hint.
    for entry in UploadJobFile.objects.filter(upload_job=job).order_by(
        "-is_directory", "pk"
    ):
        path = inventory_path(entry.path)
        if entry.is_directory:
            if path.is_symlink() or path.resolve() != path:
                raise ValueError("Symbolic link in registered working directory")
            for child in path.rglob("*"):
                if child.is_symlink():
                    raise ValueError("Symbolic link in registered working directory")
                if not child.is_dir():
                    files[child] = UploadFileReference(
                        child, UploadJobFile.Role(entry.role), "", registered=True
                    )
            continue
        files[path] = UploadFileReference(
            path, UploadJobFile.Role(entry.role), "", registered=True
        )
    return tuple(files.values())


def register_upload_job_sources(job: UploadJob) -> None:
    for reference in upload_job_file_inventory(job):
        # Never refresh old identities merely because cleanup/retry reads them.
        if not reference.registered:
            register_upload_job_file(job.pk, reference.path, role=reference.role)


def registered_staging_paths(paths: tuple[Path | None, ...]) -> tuple[Path | None, ...]:
    """Include external-tool side outputs from the same explicitly owned workspace."""
    expanded = dict.fromkeys(paths)
    ancestors = {
        str(parent)
        for path in paths
        if path is not None
        for parent in (inventory_path(path), *inventory_path(path).parents)
    }
    if not ancestors:
        return tuple(expanded)
    directories = UploadJobFile.objects.filter(
        is_directory=True, role=UploadJobFile.Role.WORKING, path__in=ancestors
    ).select_related("upload_job")
    inventories: dict[UUID, tuple[UploadFileReference, ...]] = {}
    for directory in directories:
        job = directory.upload_job
        if job.pk not in inventories:
            inventories[job.pk] = upload_job_file_inventory(job)
        root = Path(directory.path)
        for reference in inventories[job.pk]:
            if (
                reference.role == UploadJobFile.Role.WORKING
                and reference.path.is_relative_to(root)
            ):
                expanded[reference.path] = None
    return tuple(expanded)


def _observe_job_operation(
    job_id: UUID,
    guard: Callable[[], None] | None,
    operation: str,
    status: str,
    source: Path | None,
    destination: Path | None,
) -> None:
    if status not in {"ok", "planned"} or operation not in {
        "copy",
        "report_source_snapshot",
        "move",
        "move_path",
        "write",
        "create",
        "handoff",
        "unlink",
        "rmtree",
        "storage_delete",
        "temporary",
    }:
        return
    if guard is not None:
        guard()
    if destination is not None:
        if destination.is_dir():
            for child in destination.rglob("*"):
                if child.is_symlink():
                    raise ValueError("Symbolic link in moved artifact directory")
                if not child.is_dir():
                    register_upload_job_file(job_id, child)
        else:
            register_upload_job_file(
                job_id,
                destination,
                planned=status == "planned",
                role=UploadJobFile.Role.WORKING if operation == "temporary" else None,
            )
    if source is not None and not source.exists() and not source.is_symlink():
        mark_inventory_file_removed(source)
        if operation in {"rmtree", "move_path"}:
            for entry in UploadJobFile.objects.filter(
                upload_job_id=job_id, removed_at__isnull=True
            ):
                if Path(entry.path).is_relative_to(source):
                    mark_inventory_file_removed(Path(entry.path))


@contextmanager
def track_upload_job_files(
    job: UploadJob, *, guard: Callable[[], None] | None = None
) -> Generator[None]:
    register_upload_job_sources(job)
    token = _current_job.set(job.pk)

    def observe(
        operation: str, status: str, source: Path | None, destination: Path | None
    ) -> None:
        _observe_job_operation(job.pk, guard, operation, status, source, destination)

    try:
        with file_inventory_scope(observe):
            yield
    finally:
        _current_job.reset(token)
        for entry in UploadJobFile.objects.filter(
            upload_job=job, removed_at__isnull=True
        ):
            path = Path(entry.path)
            if not path.exists() and not path.is_symlink():
                mark_inventory_file_removed(path)


def record_media_files(job: UploadJob, media: VideoFile | RawPdfFile) -> None:
    fields = (
        (media.raw_file, media.processed_file)
        if isinstance(media, VideoFile)
        else (media.file, media.processed_file)
    )
    for field in fields:
        if field.name:
            register_upload_job_file(
                job.pk, Path(field.path), role=UploadJobFile.Role.RETAINED
            )
    if isinstance(media, VideoFile):
        for frame in Frame.objects.filter(video=media, is_extracted=True).exclude(
            relative_path=""
        ):
            register_upload_job_file(
                job.pk, frame.file_path, role=UploadJobFile.Role.RETAINED
            )
        for receipt in cleanup_receipts(media.meta):
            for name in (receipt.source_name, receipt.replacement_name):
                register_upload_job_file(
                    job.pk, inventory_path(name), role=UploadJobFile.Role.RETAINED
                )
        for name in (
            media.raw_streamable_relative_path,
            media.processed_streamable_relative_path,
        ):
            if name:
                register_upload_job_file(
                    job.pk, inventory_path(name), role=UploadJobFile.Role.RETAINED
                )
        for artifact in VideoHlsArtifact.objects.filter(video=media):
            if artifact.playlist_relative_path:
                register_upload_job_file(
                    job.pk,
                    inventory_path(artifact.playlist_relative_path),
                    role=UploadJobFile.Role.RETAINED,
                )
            if artifact.segment_directory_relative_path:
                directory = inventory_path(artifact.segment_directory_relative_path)
                if directory.is_symlink() or directory.resolve() != directory:
                    raise ValueError("Symbolic link in media inventory")
                for child in directory.rglob("*"):
                    if child.is_file():
                        register_upload_job_file(
                            job.pk, child, role=UploadJobFile.Role.RETAINED
                        )


def validate_registered_staging_cleanup(path: Path) -> None:
    """A registered canonical artifact or another job's file is never staging."""
    entries = list(_registered_entries(path).select_related("upload_job"))
    if not entries or not path.exists():
        return
    current_job = _current_job.get()
    if len({entry.upload_job.pk for entry in entries}) != 1 or (
        current_job is not None and entries[0].upload_job.pk != current_job
    ):
        raise ValueError("Staging file is shared with another upload job")
    if any(entry.role not in DISPOSABLE_ROLES for entry in entries):
        raise ValueError("Staging inventory role does not permit deletion")
    job = entries[0].upload_job
    if current_job is None and (
        job.status in {UploadJob.Status.PROCESSING, UploadJob.Status.RETRYING}
        or job.retryable
        or (
            job.processing_lease_expires_at is not None
            and job.processing_lease_expires_at > timezone.now()
        )
    ):
        raise ValueError("An active upload job owns the staging file")


def _registered_entries(path: Path) -> QuerySet[UploadJobFile]:
    target = inventory_path(path)
    return UploadJobFile.objects.filter(
        Q(path=str(target))
        | Q(is_directory=True, path__in=[str(parent) for parent in target.parents]),
        removed_at__isnull=True,
    )


def has_other_file_owner(job: UploadJob, path: Path) -> bool:
    return _registered_entries(path).exclude(upload_job=job).exists()


__all__ = [
    "DISPOSABLE_ROLES",
    "has_other_file_owner",
    "LEGACY_FILE_FIELDS",
    "UploadFileReference",
    "inventory_path",
    "mark_inventory_file_removed",
    "record_media_files",
    "record_working_file",
    "register_upload_job_file",
    "register_upload_job_sources",
    "track_upload_job_files",
    "registered_staging_paths",
    "track_working_directory",
    "upload_job_file_inventory",
    "validate_registered_staging_cleanup",
]
