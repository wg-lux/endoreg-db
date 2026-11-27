"""Reclaim abandoned attempt artifacts from the protected transcoding root.

Temporary transcode artifacts live for one attempt and are normally removed by
their attempt's ``finally`` path. A killed worker leaves them behind. An entry
is deleted only when its owner is identified and proven terminal and the entry
has not changed for the configured grace period. Process-scoped scratch and
unknown layouts carry no persisted owner and are only reported.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from django.db.models import Q
from lx_dtypes.models.contracts.json_types import JsonValue

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.models.hub.upload_job_file import UploadJobFile
from endoreg_db.models.media.video.hls_artifact import VideoHlsArtifact
from endoreg_db.models.media.video.video_file import VideoFile
from endoreg_db.models.media.video.video_processing import VideoProcessingHistory
from endoreg_db.import_files.context.file_lock import (
    content_hash_lock,
    file_lock_path,
)
from endoreg_db.services.hub.upload_job_files import (
    DISPOSABLE_ROLES,
    mark_inventory_file_removed,
    upload_job_owns_active_attempt,
)
from endoreg_db.services.media.operation_gate import (
    video_has_active_media_operation_leases,
)
from endoreg_db.utils.file_operations import safe_rmtree, safe_unlink_file
from endoreg_db.utils.paths import get_runtime_paths
from endoreg_db.utils.structured_logging import emit_structured_event

logger = logging.getLogger(__name__)

HLS_BUILD_ROLES = ("hls_key_material", "hls_output", "hls_plaintext_source")
PROCESSED_TRANSCODE_ROLE = "processed_storage_pressure"
# Context-managed scratch whose creating process is not persisted anywhere.
PROCESS_SCOPED_PREFIXES = (
    "endoreg-fieldfile-",
    "upload-source-",
    "report-",
    "frame-provenance-",
    "processed-transcode-source-",
    "sap_ish_import_",
)
_RAW_HASH_NAME = re.compile(r"^(?:anonymizing_)?(?P<raw_hash>[0-9a-f]{64})(?:$|[._])")
_UUID_HEX_NAME = re.compile(r"^[0-9a-f]{32}(?:\.[A-Za-z0-9]+)?$")
_ACTIVE_HLS_STATUSES = (
    VideoHlsArtifact.Status.QUEUED,
    VideoHlsArtifact.Status.MATERIALIZING,
    VideoHlsArtifact.Status.VALIDATED,
)


class TransientArtifactOwnerKind(StrEnum):
    UPLOAD_JOB_INVENTORY = "upload_job_inventory"
    HLS_BUILD = "hls_build"
    VIDEO_RAW_HASH = "video_raw_hash"
    PROCESSED_TRANSCODE = "processed_transcode"
    PROCESS_SCOPED = "process_scoped"
    UNKNOWN = "unknown"


class TransientArtifactDecision(StrEnum):
    DELETE = "delete"
    OWNER_ACTIVE = "owner_active"
    OWNER_CONFLICT = "owner_conflict"
    TOO_RECENT = "too_recent"
    UNPROVEN_OWNER = "unproven_owner"
    UNSAFE_LINK = "unsafe_link"


@dataclass(frozen=True, slots=True)
class StagingTreeSnapshot:
    size_bytes: int
    newest_mtime: float
    contains_symlink: bool


@dataclass(frozen=True, slots=True)
class TransientArtifactItem:
    path: Path
    owner_kind: TransientArtifactOwnerKind
    decision: TransientArtifactDecision
    size_bytes: int
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class TransientArtifactCleanupResult:
    items: tuple[TransientArtifactItem, ...]

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
            if item.decision is TransientArtifactDecision.DELETE
        )

    @property
    def retained_bytes(self) -> int:
        return sum(
            item.size_bytes
            for item in self.items
            if item.decision is not TransientArtifactDecision.DELETE
        )


def snapshot_staging_tree(path: Path) -> StagingTreeSnapshot:
    """Measure a file or directory tree without following symbolic links.

    An entry vanishing during the walk means a writer is still active, so the
    snapshot reports it as changed now and the grace period keeps it.
    """
    try:
        root = os.lstat(path)
    except FileNotFoundError:
        return StagingTreeSnapshot(0, time.time(), False)
    if Path(path).is_symlink():
        return StagingTreeSnapshot(0, root.st_mtime, True)
    if not Path(path).is_dir():
        return StagingTreeSnapshot(root.st_size, root.st_mtime, False)
    size_bytes = 0
    newest_mtime = root.st_mtime
    for directory, directory_names, file_names in os.walk(path, followlinks=False):
        for name in (*directory_names, *file_names):
            entry = Path(directory) / name
            try:
                stat_result = os.lstat(entry)
            except FileNotFoundError:
                newest_mtime = time.time()
                continue
            if entry.is_symlink():
                return StagingTreeSnapshot(size_bytes, newest_mtime, True)
            newest_mtime = max(newest_mtime, stat_result.st_mtime)
            if not entry.is_dir():
                size_bytes += stat_result.st_size
    return StagingTreeSnapshot(size_bytes, newest_mtime, False)


def staging_children(directory: Path) -> list[Path]:
    """Sorted children; a directory removed by its writer has none."""
    try:
        return sorted(directory.iterdir())
    except FileNotFoundError:
        return []


def _cleanup_units(root: Path) -> list[tuple[Path, TransientArtifactOwnerKind]]:
    """List deletion units; nested build roles are owned per video and attempt."""
    units: list[tuple[Path, TransientArtifactOwnerKind]] = []
    for child in staging_children(root):
        if child.is_dir() and not child.is_symlink() and child.name in HLS_BUILD_ROLES:
            for video_dir in staging_children(child):
                if not video_dir.is_dir() or video_dir.is_symlink():
                    units.append((video_dir, TransientArtifactOwnerKind.UNKNOWN))
                    continue
                units.extend(
                    (key_dir, TransientArtifactOwnerKind.HLS_BUILD)
                    for key_dir in staging_children(video_dir)
                )
            continue
        if (
            child.is_dir()
            and not child.is_symlink()
            and child.name == PROCESSED_TRANSCODE_ROLE
        ):
            units.extend(
                (video_dir, TransientArtifactOwnerKind.PROCESSED_TRANSCODE)
                for video_dir in staging_children(child)
            )
            continue
        if _RAW_HASH_NAME.match(child.name):
            units.append((child, TransientArtifactOwnerKind.VIDEO_RAW_HASH))
        elif child.name.startswith(PROCESS_SCOPED_PREFIXES) or _UUID_HEX_NAME.match(
            child.name
        ):
            units.append((child, TransientArtifactOwnerKind.PROCESS_SCOPED))
        else:
            units.append((child, TransientArtifactOwnerKind.UNKNOWN))
    return units


def _inventory_entries(unit: Path) -> list[UploadJobFile]:
    target = str(unit)
    ancestors = [str(parent) for parent in unit.parents]
    return list(
        UploadJobFile.objects.filter(
            Q(path=target)
            | Q(path__startswith=f"{target}/")
            | Q(is_directory=True, path__in=ancestors),
            removed_at__isnull=True,
        ).select_related("upload_job")
    )


def _inventory_decision(
    entries: list[UploadJobFile],
) -> TransientArtifactDecision | None:
    if len({entry.upload_job.pk for entry in entries}) != 1 or any(
        entry.role not in DISPOSABLE_ROLES for entry in entries
    ):
        return TransientArtifactDecision.OWNER_CONFLICT
    if upload_job_owns_active_attempt(entries[0].upload_job):
        return TransientArtifactDecision.OWNER_ACTIVE
    return None


def _video_has_active_work(video_id: int) -> bool:
    return video_has_active_media_operation_leases(video_id) or (
        VideoProcessingHistory.objects.filter(
            video_id=video_id,
            status__in=[
                VideoProcessingHistory.STATUS_PENDING,
                VideoProcessingHistory.STATUS_RUNNING,
            ],
        ).exists()
    )


def _hls_build_active(unit: Path) -> bool | None:
    """A build directory needs its artifact row; missing owners are only reported."""
    try:
        video_id = int(unit.parent.name)
        key_id = uuid.UUID(unit.name)
    except ValueError:
        return None
    statuses = set(
        VideoHlsArtifact.objects.filter(video_id=video_id, key_id=key_id).values_list(
            "status", flat=True
        )
    )
    if not statuses:
        return None
    return bool(
        statuses.intersection(_ACTIVE_HLS_STATUSES)
    ) or video_has_active_media_operation_leases(video_id)


def _processed_transcode_active(unit: Path) -> bool | None:
    try:
        video_uuid = uuid.UUID(unit.name)
    except ValueError:
        return None
    video_id = (
        VideoFile.objects.filter(uuid=video_uuid).values_list("pk", flat=True).first()
    )
    return video_id is not None and _video_has_active_work(int(video_id))


def _raw_hash(unit: Path) -> str | None:
    match = _RAW_HASH_NAME.match(unit.name)
    return None if match is None else match.group("raw_hash")


def _content_lock_held(raw_hash: str) -> bool:
    """Probe the import's content-hash lock without creating or waiting for it."""
    if not file_lock_path(raw_hash).exists():
        return False
    try:
        with content_hash_lock(raw_hash, timeout_seconds=0):
            return False
    except TimeoutError:
        return True


def _raw_hash_owner_active(unit: Path, *, content_lock_owned: bool) -> bool | None:
    """Imports and re-anonymizations hold the content-hash lock while they run.

    The lifecycle phase is deliberately not consulted: a crashed import stays
    in an in-progress phase forever, so the phase proves no running worker.
    """
    raw_hash = _raw_hash(unit)
    if raw_hash is None:
        return None
    if not content_lock_owned and _content_lock_held(raw_hash):
        return True
    # Import staging precedes the VideoFile row; an active upload owns it.
    if any(
        upload_job_owns_active_attempt(job)
        for job in UploadJob.objects.filter(content_hash=raw_hash)
    ):
        return True
    return any(
        _video_has_active_work(int(video_id))
        for video_id in VideoFile.objects.filter(raw_video_hash=raw_hash).values_list(
            "pk", flat=True
        )
    )


def classify_transient_artifact(
    unit: Path,
    owner_kind: TransientArtifactOwnerKind,
    *,
    grace_seconds: int,
    now: float,
    content_lock_owned: bool = False,
) -> TransientArtifactItem:
    """Decide one unit; ``content_lock_owned`` means the caller holds its lock."""
    snapshot = snapshot_staging_tree(unit)

    def _item(
        kind: TransientArtifactOwnerKind, decision: TransientArtifactDecision
    ) -> TransientArtifactItem:
        return TransientArtifactItem(unit, kind, decision, snapshot.size_bytes)

    if snapshot.contains_symlink:
        return _item(owner_kind, TransientArtifactDecision.UNSAFE_LINK)
    entries = _inventory_entries(unit)
    if entries:
        owner_kind = TransientArtifactOwnerKind.UPLOAD_JOB_INVENTORY
        blocked = _inventory_decision(entries)
        if blocked is not None:
            return _item(owner_kind, blocked)
    else:
        match owner_kind:
            case TransientArtifactOwnerKind.HLS_BUILD:
                active = _hls_build_active(unit)
            case TransientArtifactOwnerKind.PROCESSED_TRANSCODE:
                active = _processed_transcode_active(unit)
            case TransientArtifactOwnerKind.VIDEO_RAW_HASH:
                active = _raw_hash_owner_active(
                    unit, content_lock_owned=content_lock_owned
                )
            case (
                TransientArtifactOwnerKind.PROCESS_SCOPED
                | TransientArtifactOwnerKind.UNKNOWN
                | TransientArtifactOwnerKind.UPLOAD_JOB_INVENTORY
            ):
                active = None
        if active is None:
            return _item(owner_kind, TransientArtifactDecision.UNPROVEN_OWNER)
        if active:
            return _item(owner_kind, TransientArtifactDecision.OWNER_ACTIVE)
    if now - snapshot.newest_mtime < grace_seconds:
        return _item(owner_kind, TransientArtifactDecision.TOO_RECENT)
    return _item(owner_kind, TransientArtifactDecision.DELETE)


def _delete_unit(item: TransientArtifactItem) -> None:
    entries = (
        _inventory_entries(item.path)
        if item.owner_kind is TransientArtifactOwnerKind.UPLOAD_JOB_INVENTORY
        else []
    )
    if item.path.is_dir():
        safe_rmtree(item.path, missing_ok=False)
    else:
        safe_unlink_file(item.path, missing_ok=False)
    for entry in entries:
        entry_path = Path(entry.path)
        if entry_path == item.path or entry_path.is_relative_to(item.path):
            mark_inventory_file_removed(entry_path)


def _revalidate_and_delete(
    item: TransientArtifactItem, *, grace_seconds: int
) -> TransientArtifactItem:
    """Re-decide under the owner's fence, then delete within it."""
    raw_hash = (
        _raw_hash(item.path)
        if item.owner_kind is TransientArtifactOwnerKind.VIDEO_RAW_HASH
        else None
    )
    # Holding the content-hash lock stops an import from starting mid-deletion.
    fence = (
        content_hash_lock(raw_hash, timeout_seconds=0)
        if raw_hash is not None
        else nullcontext()
    )
    try:
        with fence:
            current = classify_transient_artifact(
                item.path,
                item.owner_kind,
                grace_seconds=grace_seconds,
                now=time.time(),
                content_lock_owned=raw_hash is not None,
            )
            if current.decision is not TransientArtifactDecision.DELETE:
                return current
            _delete_unit(current)
    except TimeoutError:
        return TransientArtifactItem(
            item.path,
            item.owner_kind,
            TransientArtifactDecision.OWNER_ACTIVE,
            item.size_bytes,
        )
    emit_structured_event(
        logger,
        "transient_artifact_deleted",
        path=str(current.path),
        owner_kind=current.owner_kind.value,
        size_bytes=current.size_bytes,
    )
    return TransientArtifactItem(
        current.path,
        current.owner_kind,
        current.decision,
        current.size_bytes,
        deleted=True,
    )


def list_transient_artifact_units() -> list[tuple[Path, TransientArtifactOwnerKind]]:
    """Every unit the transient reaper classifies, with its layout owner kind."""
    root = get_runtime_paths().transcoding
    return _cleanup_units(root) if root.exists() else []


def reap_transient_artifact(unit: Path, *, grace_seconds: int) -> TransientArtifactItem:
    """Delete one listed unit if its owner is still proven terminal."""
    for candidate, owner_kind in list_transient_artifact_units():
        if candidate == unit:
            item = classify_transient_artifact(
                unit, owner_kind, grace_seconds=grace_seconds, now=time.time()
            )
            if item.decision is not TransientArtifactDecision.DELETE:
                return item
            return _revalidate_and_delete(item, grace_seconds=grace_seconds)
    raise ValueError("Path is not a transient artifact unit")


def run_transient_artifact_cleanup(
    *,
    apply: bool,
    grace_seconds: int,
    limit: int = 25,
) -> TransientArtifactCleanupResult:
    """Classify every unit; delete at most ``limit`` revalidated terminal units."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    now = time.time()
    items: list[TransientArtifactItem] = []
    deleted = 0
    for unit, owner_kind in list_transient_artifact_units():
        item = classify_transient_artifact(
            unit, owner_kind, grace_seconds=grace_seconds, now=now
        )
        if (
            apply
            and item.decision is TransientArtifactDecision.DELETE
            and deleted < limit
        ):
            item = _revalidate_and_delete(item, grace_seconds=grace_seconds)
            if item.deleted:
                deleted += 1
        items.append(item)
    result = TransientArtifactCleanupResult(items=tuple(items))
    by_decision: dict[str, int] = {}
    for item in result.items:
        key = f"{item.owner_kind.value}:{item.decision.value}"
        by_decision[key] = by_decision.get(key, 0) + item.size_bytes
    bytes_by_owner_and_decision: dict[str, JsonValue] = dict(by_decision)
    emit_structured_event(
        logger,
        "transient_artifact_cleanup_summary",
        apply=apply,
        deleted_count=result.deleted_count,
        freed_bytes=result.freed_bytes,
        reclaimable_bytes=result.reclaimable_bytes,
        retained_bytes=result.retained_bytes,
        bytes_by_owner_and_decision=bytes_by_owner_and_decision,
    )
    return result


__all__ = [
    "StagingTreeSnapshot",
    "TransientArtifactCleanupResult",
    "TransientArtifactDecision",
    "TransientArtifactItem",
    "TransientArtifactOwnerKind",
    "classify_transient_artifact",
    "list_transient_artifact_units",
    "reap_transient_artifact",
    "run_transient_artifact_cleanup",
    "snapshot_staging_tree",
    "staging_children",
]
