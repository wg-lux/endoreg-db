"""Reclaim upload staging directories whose UploadJob no longer exists.

Uploads are stored as ``<tier>/<id prefix>/<upload job id>/<file>``. A job
directory without its UploadJob row and without any remaining reference is an
orphan, for example after a failed insert or a deleted job. Directories whose
job still exists belong to the ledger reaper in ``cleanup.py``; files outside
this layout are only reported.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from django.db.models import Q

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.services.hub.cleanup import stored_media_name_referenced
from endoreg_db.services.hub.upload_job_files import LEGACY_FILE_FIELDS
from endoreg_db.services.hub.watcher_handoff import is_in_progress_handoff_path
from endoreg_db.services.runtime.transient_artifact_cleanup import (
    snapshot_staging_tree,
    staging_children,
)
from endoreg_db.utils.file_operations import safe_rmtree
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)


class UploadStagingOrphanDecision(StrEnum):
    DELETE = "delete"
    JOB_EXISTS = "job_exists"
    REFERENCED = "referenced"
    IN_PROGRESS_HANDOFF = "in_progress_handoff"
    UNSAFE_LINK = "unsafe_link"
    TOO_RECENT = "too_recent"
    UNRECOGNIZED_LAYOUT = "unrecognized_layout"


@dataclass(frozen=True, slots=True)
class UploadStagingOrphanItem:
    path: Path
    decision: UploadStagingOrphanDecision
    size_bytes: int
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class UploadStagingOrphanResult:
    items: tuple[UploadStagingOrphanItem, ...]

    @property
    def deleted_count(self) -> int:
        return sum(1 for item in self.items if item.deleted)

    @property
    def freed_bytes(self) -> int:
        return sum(item.size_bytes for item in self.items if item.deleted)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(
            item.size_bytes
            for item in self.items
            if item.decision is UploadStagingOrphanDecision.DELETE
        )

    @property
    def retained_bytes(self) -> int:
        return sum(
            item.size_bytes
            for item in self.items
            if item.decision is not UploadStagingOrphanDecision.DELETE
        )


def _tier_roots() -> tuple[Path, ...]:
    paths = get_runtime_paths()
    return (paths.upload_api, paths.upload_watcher, paths.upload_preanonymized)


def _staging_units(root: Path) -> list[tuple[Path, uuid.UUID | None]]:
    """Return job directories with their id; other entries carry ``None``."""
    units: list[tuple[Path, uuid.UUID | None]] = []
    for prefix_dir in staging_children(root):
        if not prefix_dir.is_dir() or prefix_dir.is_symlink():
            units.append((prefix_dir, None))
            continue
        for job_dir in staging_children(prefix_dir):
            job_id: uuid.UUID | None = None
            if job_dir.is_dir() and not job_dir.is_symlink():
                try:
                    job_id = uuid.UUID(job_dir.name)
                except ValueError:
                    job_id = None
            if job_id is not None and (
                str(job_id) != job_dir.name or job_dir.name[:2] != prefix_dir.name
            ):
                job_id = None
            units.append((job_dir, job_id))
    return units


def _is_referenced(job_dir: Path) -> bool:
    storage_root = get_runtime_paths().storage
    absolute_prefix = f"{job_dir}/"
    relative_prefix = f"{job_dir.relative_to(storage_root).as_posix()}/"
    provenance_reference = Q()
    for field in LEGACY_FILE_FIELDS:
        for prefix in (absolute_prefix, relative_prefix):
            provenance_reference |= Q(
                **{f"processing_provenance__{field}__startswith": prefix}
            )
    return (
        stored_media_name_referenced(relative_prefix, prefix=True)
        or UploadJob.objects.filter(provenance_reference).exists()
        # Removed entries also count: a file recorded as deleted that exists
        # again is a database/filesystem mismatch, not disposable data.
        or UploadJobFile.objects.filter(
            Q(path=str(job_dir)) | Q(path__startswith=absolute_prefix)
        ).exists()
    )


def classify_upload_staging_unit(
    unit: Path,
    job_id: uuid.UUID | None,
    *,
    grace_seconds: int,
    now: float,
) -> UploadStagingOrphanItem:
    snapshot = snapshot_staging_tree(unit)

    def _item(decision: UploadStagingOrphanDecision) -> UploadStagingOrphanItem:
        return UploadStagingOrphanItem(unit, decision, snapshot.size_bytes)

    if job_id is None:
        return _item(UploadStagingOrphanDecision.UNRECOGNIZED_LAYOUT)
    if snapshot.contains_symlink:
        return _item(UploadStagingOrphanDecision.UNSAFE_LINK)
    if UploadJob.objects.filter(pk=job_id).exists():
        return _item(UploadStagingOrphanDecision.JOB_EXISTS)
    if any(is_in_progress_handoff_path(path) for path in unit.rglob("*")):
        return _item(UploadStagingOrphanDecision.IN_PROGRESS_HANDOFF)
    if _is_referenced(unit):
        return _item(UploadStagingOrphanDecision.REFERENCED)
    if now - snapshot.newest_mtime < grace_seconds:
        return _item(UploadStagingOrphanDecision.TOO_RECENT)
    return _item(UploadStagingOrphanDecision.DELETE)


def _revalidate_and_delete(
    unit: Path, job_id: uuid.UUID | None, *, grace_seconds: int
) -> UploadStagingOrphanItem:
    # A job row or reference may appear between scan and mutation.
    item = classify_upload_staging_unit(
        unit, job_id, grace_seconds=grace_seconds, now=time.time()
    )
    if item.decision is not UploadStagingOrphanDecision.DELETE:
        return item
    safe_rmtree(unit, missing_ok=False)
    emit_structured_event(
        logger,
        "upload_staging_orphan_deleted",
        path=str(item.path),
        upload_job_id=str(job_id),
        size_bytes=item.size_bytes,
    )
    return UploadStagingOrphanItem(
        item.path, item.decision, item.size_bytes, deleted=True
    )


def list_upload_staging_units() -> list[tuple[Path, uuid.UUID | None]]:
    """Every unit the orphan reaper classifies, across all upload tiers."""
    return [
        unit for root in _tier_roots() if root.exists() for unit in _staging_units(root)
    ]


def reap_upload_staging_orphan(
    unit: Path, *, grace_seconds: int
) -> UploadStagingOrphanItem:
    """Delete one listed job directory if it is still a verified orphan."""
    for candidate, job_id in list_upload_staging_units():
        if candidate == unit:
            return _revalidate_and_delete(unit, job_id, grace_seconds=grace_seconds)
    raise ValueError("Path is not an upload staging unit")


def run_upload_staging_orphan_cleanup(
    *,
    apply: bool,
    grace_seconds: int,
    limit: int = 25,
) -> UploadStagingOrphanResult:
    """Classify every job directory; delete at most ``limit`` revalidated orphans."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    now = time.time()
    items: list[UploadStagingOrphanItem] = []
    deleted = 0
    for unit, job_id in list_upload_staging_units():
        item = classify_upload_staging_unit(
            unit, job_id, grace_seconds=grace_seconds, now=now
        )
        if (
            apply
            and item.decision is UploadStagingOrphanDecision.DELETE
            and deleted < limit
        ):
            item = _revalidate_and_delete(unit, job_id, grace_seconds=grace_seconds)
            deleted += int(item.deleted)
        items.append(item)
    result = UploadStagingOrphanResult(items=tuple(items))
    emit_structured_event(
        logger,
        "upload_staging_orphan_cleanup_summary",
        apply=apply,
        deleted_count=result.deleted_count,
        freed_bytes=result.freed_bytes,
        reclaimable_bytes=result.reclaimable_bytes,
        retained_bytes=result.retained_bytes,
        unrecognized_layout_bytes=sum(
            item.size_bytes
            for item in result.items
            if item.decision is UploadStagingOrphanDecision.UNRECOGNIZED_LAYOUT
        ),
    )
    return result


__all__ = [
    "UploadStagingOrphanDecision",
    "UploadStagingOrphanItem",
    "UploadStagingOrphanResult",
    "classify_upload_staging_unit",
    "list_upload_staging_units",
    "reap_upload_staging_orphan",
    "run_upload_staging_orphan_cleanup",
]
