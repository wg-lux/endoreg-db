"""Read access to the files of completed frame annotation export jobs.

The browser copies a completed run into a folder the user picks on their own
machine. Only the latest job of a run directory is downloadable, because a
later job may rewrite that directory for another scope. Files are listed and
served read-only; symbolic links anywhere in a run fail loudly instead of
being followed or skipped.
"""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from django.core.exceptions import ValidationError

from endoreg_db.models.media.video.frame_annotation_export_job import (
    FrameAnnotationExportJob,
)
from endoreg_db.services.media.frame_annotation_export_jobs import (
    ExportUser,
    frame_annotation_export_root,
    run_lock_path,
    visible_frame_annotation_export_jobs,
)
from endoreg_db.utils.paths import normalize_protected_media_relative_path
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)

_ARCHIVE_CHUNK_SIZE = 1024 * 1024


class ExportRunNotFound(LookupError):
    """The job does not exist within the user's center scope."""


class ExportRunUnavailable(RuntimeError):
    """The job exists but its run directory must not be served."""


@dataclass(frozen=True, slots=True)
class ExportRunFile:
    """One regular file, relative to its run directory."""

    relative_path: str
    size: int


@dataclass(frozen=True, slots=True)
class CompletedExportRun:
    job: FrameAnnotationExportJob
    run_dir: Path


def completed_export_run(job_id: UUID, *, user: ExportUser) -> CompletedExportRun:
    """Return the run directory of a visible, completed and current job.

    Raises ``ExportRunNotFound`` outside the user's scope and
    ``ExportRunUnavailable`` while the job is unfinished, superseded by a
    newer job for the same directory, or its directory is missing.
    """
    job = visible_frame_annotation_export_jobs(user).filter(pk=job_id).first()
    if job is None:
        raise ExportRunNotFound(f"Export job {job_id} not found.")
    if job.status != FrameAnnotationExportJob.Status.COMPLETED.value:
        raise ExportRunUnavailable(f"Export job {job_id} is {job.status}.")
    newer = FrameAnnotationExportJob.objects.filter(
        output_dir=job.output_dir, created_at__gt=job.created_at
    ).exclude(pk=job.pk)
    if newer.exists():
        raise ExportRunUnavailable(
            f"A newer export job uses run directory {job.output_dir}."
        )
    # Validates the stored run name as one safe path segment.
    run_lock_path(job.output_dir)
    root = frame_annotation_export_root()
    run_dir = root / job.output_dir
    if run_dir.is_symlink() or run_dir.resolve() != run_dir or not run_dir.is_dir():
        raise ExportRunUnavailable(
            f"Run directory {job.output_dir} is missing or not a plain directory."
        )
    return CompletedExportRun(job=job, run_dir=run_dir)


def list_export_run_files(run: CompletedExportRun) -> tuple[ExportRunFile, ...]:
    """List every regular file of the run in stable path order."""
    files: list[ExportRunFile] = []
    for directory, dir_names, file_names in run.run_dir.walk():
        for name in (*dir_names, *file_names):
            if (directory / name).is_symlink():
                raise ExportRunUnavailable(
                    f"Run directory {run.job.output_dir} contains a symbolic link."
                )
        dir_names.sort()
        for name in sorted(file_names):
            path = directory / name
            if not path.is_file():
                raise ExportRunUnavailable(
                    f"Run directory {run.job.output_dir} contains a special file."
                )
            files.append(
                ExportRunFile(
                    relative_path=path.relative_to(run.run_dir).as_posix(),
                    size=path.stat().st_size,
                )
            )
    return tuple(files)


def resolve_export_run_file(run: CompletedExportRun, relative_path: str) -> Path:
    """Return the absolute path of one regular file inside the run.

    Raises ``ValidationError`` for unsafe paths and ``ExportRunNotFound`` for
    paths that are not a regular file of the run.
    """
    try:
        normalized = normalize_protected_media_relative_path(relative_path)
    except ValueError as exc:
        raise ValidationError(f"Invalid export file path: {exc}") from exc
    candidate = run.run_dir / normalized
    # A resolved path that differs from the lexical one passed a symbolic link.
    if candidate.resolve() != candidate or not candidate.is_file():
        raise ExportRunNotFound(f"Export file {normalized} not found.")
    return candidate


def iter_export_run_archive(
    run: CompletedExportRun, files: tuple[ExportRunFile, ...]
) -> Iterator[bytes]:
    """Stream the run as an uncompressed ZIP archive below a folder of its name.

    Frames are already compressed images, so storing avoids pointless CPU work.
    """
    sink = _ArchiveSink()
    with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for entry in files:
            source = resolve_export_run_file(run, entry.relative_path)
            arcname = f"{run.job.output_dir}/{entry.relative_path}"
            with (
                source.open("rb") as handle,
                archive.open(arcname, mode="w", force_zip64=True) as member,
            ):
                while chunk := handle.read(_ARCHIVE_CHUNK_SIZE):
                    member.write(chunk)
                    yield sink.drain()
            yield sink.drain()
    yield sink.drain()


def log_export_run_access(
    run: CompletedExportRun, *, user: ExportUser, access: str, file_count: int
) -> None:
    emit_structured_event(
        logger,
        "frame_annotation_export_run_downloaded",
        job_id=str(run.job.pk),
        output_dir=run.job.output_dir,
        access=access,
        file_count=file_count,
        user_id=user.pk,
    )


class _ArchiveSink:
    """Write-only, non-seekable buffer so ZipFile emits data descriptors."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data
