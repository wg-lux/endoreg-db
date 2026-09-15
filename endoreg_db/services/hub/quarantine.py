from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, cast

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.models.hub.quarantine_item import QuarantineItem
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.audit import emit_hub_audit_event
from endoreg_db.utils.file_operations import ensure_directory, safe_unlink_file
from endoreg_db.utils.paths import EndoregPathsModel

if TYPE_CHECKING:
    from django.contrib.auth.models import User


@dataclass(frozen=True)
class QuarantineSyncResult:
    quarantine_dir: Path
    scanned_count: int
    created_count: int
    updated_count: int
    missing_count: int
    total_bytes: int


@dataclass(frozen=True)
class QuarantineApprovalResult:
    approved_count: int
    approved_items: tuple[QuarantineItem, ...]


@dataclass(frozen=True)
class QuarantineReapResult:
    quarantine_dir: Path
    dry_run: bool
    candidate_count: int
    candidate_bytes: int
    deleted_count: int
    candidates: tuple[QuarantineItem, ...]
    deleted: tuple[QuarantineItem, ...]
    missing_count: int


def quarantine_dir() -> Path:
    return EndoregPathsModel.from_environment().quarantine


def _now() -> datetime:
    return timezone.now()


def _quarantined_at_from_stat(mtime_seconds: float) -> datetime:
    return datetime.fromtimestamp(mtime_seconds, tz=timezone.get_current_timezone())


def _cutoff_datetime(*, older_than_days: int, now: datetime) -> datetime:
    if older_than_days < 0:
        raise ValueError("older_than_days must not be negative")
    return now - timedelta(days=older_than_days)


def _validate_quarantine_path(path: Path, *, root: Path | None = None) -> Path:
    root_path = ensure_directory(root or quarantine_dir()).resolve()
    resolved = Path(path).resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(
            f"Quarantine path must stay inside {root_path}: {path}"
        ) from exc
    return resolved


def _relative_quarantine_path(path: Path, *, root: Path | None = None) -> str:
    root_path = ensure_directory(root or quarantine_dir()).resolve()
    resolved = _validate_quarantine_path(path, root=root_path)
    return resolved.relative_to(root_path).as_posix()


def _active_statuses() -> tuple[str, ...]:
    return (
        QuarantineItem.Status.PENDING_REVIEW.value,
        QuarantineItem.Status.RETAINED.value,
        QuarantineItem.Status.APPROVED_FOR_DELETION.value,
        QuarantineItem.Status.FAILED.value,
    )


def _metadata_for_file(
    *,
    source_event: str,
    source_system: str | None,
    reason: str | None,
    file_mtime_ns: int,
    upload_job: UploadJob | None,
) -> JsonObject:
    metadata: JsonObject = {
        "source_event": source_event,
        "file_mtime_ns": file_mtime_ns,
        "discovered_by": "quarantine_sync",
    }
    if source_system:
        metadata["source_system"] = source_system
    if reason:
        metadata["reason"] = reason
    if upload_job is not None:
        metadata["upload_job_id"] = str(upload_job.pk)
        if upload_job.content_type:
            metadata["content_type"] = upload_job.content_type
    return metadata


def index_quarantine_file(
    path: Path | str,
    *,
    root: Path | None = None,
    source_event: str = "quarantine.discovered",
    source_system: str | None = None,
    reason: str | None = None,
    upload_job: UploadJob | None = None,
    now: datetime | None = None,
) -> tuple[QuarantineItem, bool]:
    quarantine_root = ensure_directory(root or quarantine_dir()).resolve()
    resolved = _validate_quarantine_path(Path(path), root=quarantine_root)
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"Quarantine path is not an existing file: {resolved}")

    stat_result = resolved.stat()
    seen_at = now or _now()
    metadata = _metadata_for_file(
        source_event=source_event,
        source_system=source_system,
        reason=reason,
        file_mtime_ns=stat_result.st_mtime_ns,
        upload_job=upload_job,
    )
    relative_path = _relative_quarantine_path(resolved, root=quarantine_root)
    quarantined_at = _quarantined_at_from_stat(stat_result.st_mtime)
    defaults = {
        "relative_path": relative_path,
        "original_filename": resolved.name,
        "size_bytes": stat_result.st_size,
        "file_mtime_ns": stat_result.st_mtime_ns,
        "quarantined_at": quarantined_at,
        "last_seen_at": seen_at,
        "metadata": metadata,
        "source_upload_job": upload_job,
    }
    item, created = QuarantineItem.objects.get_or_create(
        path=str(resolved),
        defaults=defaults,
    )
    if created:
        emit_hub_audit_event(
            "hub.quarantine_item_indexed",
            quarantine_item_id=str(item.pk),
            relative_path=item.relative_path,
            source_event=source_event,
        )
        return item, True

    item.relative_path = relative_path
    item.original_filename = resolved.name
    item.size_bytes = stat_result.st_size
    item.file_mtime_ns = stat_result.st_mtime_ns
    item.quarantined_at = quarantined_at
    item.last_seen_at = seen_at
    item.metadata = {**item.metadata, **metadata}
    if upload_job is not None:
        item.source_upload_job = upload_job
    if item.status in {
        QuarantineItem.Status.DELETED.value,
        QuarantineItem.Status.MISSING.value,
    }:
        item.status = QuarantineItem.Status.PENDING_REVIEW.value
        item.deleted_at = None
        item.error_detail = ""
    item.save()
    return item, False


def sync_quarantine_inventory(*, now: datetime | None = None) -> QuarantineSyncResult:
    root = ensure_directory(quarantine_dir()).resolve()
    sync_now = now or _now()
    seen_paths: set[str] = set()
    scanned_count = 0
    created_count = 0
    updated_count = 0
    total_bytes = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        item, created = index_quarantine_file(path, now=sync_now)
        scanned_count += 1
        seen_paths.add(item.path)
        total_bytes += item.size_bytes
        if created:
            created_count += 1
        else:
            updated_count += 1

    missing_count = 0
    active_items = QuarantineItem.objects.filter(
        status__in=_active_statuses(),
        path__startswith=f"{root.as_posix()}/",
    )
    for item in active_items:
        if item.path in seen_paths:
            continue
        if Path(item.path).exists():
            continue
        item.status = QuarantineItem.Status.MISSING.value
        item.error_detail = "Quarantine file was not found during inventory sync."
        item.last_seen_at = sync_now
        item.save(
            update_fields=["status", "error_detail", "last_seen_at", "updated_at"]
        )
        missing_count += 1

    return QuarantineSyncResult(
        quarantine_dir=root,
        scanned_count=scanned_count,
        created_count=created_count,
        updated_count=updated_count,
        missing_count=missing_count,
        total_bytes=total_bytes,
    )


def list_quarantine_items(
    *,
    status: str | None = None,
    older_than_days: int | None = None,
    limit: int = 100,
    offset: int = 0,
    now: datetime | None = None,
) -> tuple[int, list[QuarantineItem]]:
    queryset: QuerySet[QuarantineItem] = QuarantineItem.objects.all()
    if status:
        queryset = queryset.filter(status=status)
    if older_than_days is not None:
        cutoff = _cutoff_datetime(older_than_days=older_than_days, now=now or _now())
        queryset = queryset.filter(quarantined_at__lte=cutoff)
    total_count = queryset.count()
    return total_count, list(queryset[offset : offset + limit])


def stale_pending_review_items(
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> list[QuarantineItem]:
    cutoff = _cutoff_datetime(older_than_days=older_than_days, now=now or _now())
    return list(
        QuarantineItem.objects.filter(
            status=QuarantineItem.Status.PENDING_REVIEW.value,
            quarantined_at__lte=cutoff,
        ).order_by("quarantined_at", "relative_path")
    )


def approve_quarantine_item(
    item: QuarantineItem,
    *,
    reason: str,
    reviewed_by: User | None = None,
    delete_after_days: int = 0,
    now: datetime | None = None,
) -> QuarantineItem:
    decision_reason = reason.strip()
    if not decision_reason:
        raise ValueError("decision_reason is required")
    if delete_after_days < 0:
        raise ValueError("delete_after_days must not be negative")
    if item.status in {
        QuarantineItem.Status.DELETED.value,
        QuarantineItem.Status.MISSING.value,
    }:
        raise ValueError(f"Cannot approve quarantine item in status {item.status}")

    decision_time = now or _now()
    item.status = QuarantineItem.Status.APPROVED_FOR_DELETION.value
    item.decision_reason = decision_reason
    item.reviewed_by = reviewed_by
    item.reviewed_at = decision_time
    item.delete_eligible_at = decision_time + timedelta(days=delete_after_days)
    item.error_detail = ""
    item.save(
        update_fields=[
            "status",
            "decision_reason",
            "reviewed_by",
            "reviewed_at",
            "delete_eligible_at",
            "error_detail",
            "updated_at",
        ]
    )
    delete_eligible_at = item.delete_eligible_at
    emit_hub_audit_event(
        "hub.quarantine_item_approved_for_deletion",
        quarantine_item_id=str(item.pk),
        relative_path=item.relative_path,
        request_user=reviewed_by,
        delete_eligible_at=(
            delete_eligible_at.isoformat() if delete_eligible_at is not None else None
        ),
    )
    return item


def retain_quarantine_item(
    item: QuarantineItem,
    *,
    reason: str,
    reviewed_by: User | None = None,
    now: datetime | None = None,
) -> QuarantineItem:
    decision_reason = reason.strip()
    if not decision_reason:
        raise ValueError("decision_reason is required")
    if item.status == QuarantineItem.Status.DELETED.value:
        raise ValueError("Cannot retain a deleted quarantine item")

    item.status = QuarantineItem.Status.RETAINED.value
    item.decision_reason = decision_reason
    item.reviewed_by = reviewed_by
    item.reviewed_at = now or _now()
    item.delete_eligible_at = None
    item.error_detail = ""
    item.save(
        update_fields=[
            "status",
            "decision_reason",
            "reviewed_by",
            "reviewed_at",
            "delete_eligible_at",
            "error_detail",
            "updated_at",
        ]
    )
    emit_hub_audit_event(
        "hub.quarantine_item_retained",
        quarantine_item_id=str(item.pk),
        relative_path=item.relative_path,
        request_user=reviewed_by,
    )
    return item


def approve_stale_quarantine_items(
    *,
    older_than_days: int,
    reason: str,
    reviewed_by: User | None = None,
    delete_after_days: int = 0,
    now: datetime | None = None,
) -> QuarantineApprovalResult:
    approved: list[QuarantineItem] = []
    decision_time = now or _now()
    with transaction.atomic():
        for item in stale_pending_review_items(
            older_than_days=older_than_days,
            now=decision_time,
        ):
            approved.append(
                approve_quarantine_item(
                    item,
                    reason=reason,
                    reviewed_by=reviewed_by,
                    delete_after_days=delete_after_days,
                    now=decision_time,
                )
            )
    return QuarantineApprovalResult(
        approved_count=len(approved),
        approved_items=tuple(approved),
    )


def _approved_delete_candidates(
    *,
    older_than_days: int,
    now: datetime,
) -> Iterable[QuarantineItem]:
    cutoff = _cutoff_datetime(older_than_days=older_than_days, now=now)
    return QuarantineItem.objects.filter(
        status=QuarantineItem.Status.APPROVED_FOR_DELETION.value,
        quarantined_at__lte=cutoff,
        delete_eligible_at__lte=now,
    ).order_by("quarantined_at", "relative_path")


def reap_approved_quarantine_items(
    *,
    older_than_days: int,
    dry_run: bool,
    now: datetime | None = None,
) -> QuarantineReapResult:
    root = ensure_directory(quarantine_dir()).resolve()
    reap_now = now or _now()
    candidates: list[QuarantineItem] = []
    deleted: list[QuarantineItem] = []
    missing_count = 0
    candidate_bytes = 0

    for item in _approved_delete_candidates(
        older_than_days=older_than_days,
        now=reap_now,
    ):
        path = _validate_quarantine_path(Path(item.path), root=root)
        if not path.exists():
            missing_count += 1
            if not dry_run:
                item.status = QuarantineItem.Status.MISSING.value
                item.error_detail = "Approved quarantine file was already missing."
                item.save(update_fields=["status", "error_detail", "updated_at"])
            continue
        if not path.is_file():
            raise ValueError(f"Approved quarantine path is not a file: {path}")
        candidates.append(item)
        candidate_bytes += path.stat().st_size
        if dry_run:
            continue

        try:
            safe_unlink_file(path, missing_ok=False)
        except Exception as exc:
            item.status = QuarantineItem.Status.FAILED.value
            item.error_detail = str(exc)
            item.save(update_fields=["status", "error_detail", "updated_at"])
            raise

        item.status = QuarantineItem.Status.DELETED.value
        item.deleted_at = reap_now
        item.error_detail = ""
        item.save(update_fields=["status", "deleted_at", "error_detail", "updated_at"])
        deleted.append(item)
        emit_hub_audit_event(
            "hub.quarantine_item_deleted",
            quarantine_item_id=str(item.pk),
            relative_path=item.relative_path,
        )

    return QuarantineReapResult(
        quarantine_dir=root,
        dry_run=dry_run,
        candidate_count=len(candidates),
        candidate_bytes=candidate_bytes,
        deleted_count=len(deleted),
        candidates=tuple(candidates),
        deleted=tuple(deleted),
        missing_count=missing_count,
    )


def user_or_none(value: object) -> User | None:
    if value is None:
        return None
    if not bool(getattr(value, "is_authenticated", False)):
        return None
    return cast("User", value)
