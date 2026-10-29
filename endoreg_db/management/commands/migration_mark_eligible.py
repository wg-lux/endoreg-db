from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypedDict, Unpack, cast
from collections.abc import Sequence

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.models.contracts.management_command import (
    MigrationMarkEligibleCommandOptionsPayload,
)

from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_integrity_lost,
)


class MigrationMarkEligibleCommandOptions(TypedDict):
    apply: bool
    limit: int
    json: bool


class MigrationUploadJobFile(Protocol):
    name: str
    storage: object


class MigrationUploadJob(Protocol):
    file: MigrationUploadJobFile
    source_file_delete_eligible_at: datetime | None
    status: str
    error_detail: str
    error_code: str
    cleanup_status: str
    source_file_persisted: bool

    def save(self, *, update_fields: Sequence[str]) -> None: ...


@dataclass(frozen=True)
class MigrationCandidateCounts:
    eligible: int
    orphaned: int


@dataclass(frozen=True)
class MigrationUpdateResult:
    eligible: bool
    marked_lost: bool
    repaired_orphaned: bool
    skipped_live_lease: bool = False


@dataclass(frozen=True)
class MigrationUpdateCounts:
    updated_eligible: int
    marked_lost: int
    repaired_orphaned: int
    skipped_live_lease: int


class Command(BaseCommand):
    help = (
        "Mark migration-created UploadJob source files as cleanup-eligible and "
        "repair orphaned source metadata."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON summary.",
        )

    def handle(
        self,
        *args: str,
        **options: Unpack[MigrationMarkEligibleCommandOptions],
    ) -> None:
        _ = args
        options_payload = MigrationMarkEligibleCommandOptionsPayload.model_validate(
            options
        )
        jobs, total = _selected_migration_jobs(options_payload.limit)
        selected = jobs.count()
        payload: dict[str, object] = {
            "total_pending_migration_rows": total,
            "selected_rows": selected,
            "dry_run": not options_payload.apply,
            "applied": options_payload.apply,
        }

        self.stdout.write(f"Matching migration UploadJobs: {selected} / total {total}")
        candidates = _count_candidates(jobs)
        self.stdout.write(
            f"Eligible candidates: {candidates.eligible}; "
            f"orphaned candidates: {candidates.orphaned}"
        )
        payload["eligible_candidates"] = candidates.eligible
        payload["orphaned_candidates"] = candidates.orphaned

        if not options_payload.apply:
            self._write_json_payload(payload, enabled=options_payload.json_output)
            self.stdout.write("Dry run only. Re-run with --apply.")
            return

        counts = _apply_migration_job_updates(jobs, now=timezone.now())
        payload["updated_eligible"] = counts.updated_eligible
        payload["marked_lost"] = counts.marked_lost
        payload["repaired_orphaned"] = counts.repaired_orphaned
        payload["skipped_live_lease"] = counts.skipped_live_lease
        self._write_json_payload(payload, enabled=options_payload.json_output)
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated eligible={counts.updated_eligible}; "
                f"marked_lost={counts.marked_lost}; "
                f"repaired_orphaned={counts.repaired_orphaned}; "
                f"skipped_live_lease={counts.skipped_live_lease}."
            )
        )

    def _write_json_payload(
        self,
        payload: dict[str, object],
        *,
        enabled: bool,
    ) -> None:
        if enabled:
            self.stdout.write(json.dumps(payload, sort_keys=True))


def _pending_migration_jobs() -> QuerySet[UploadJob]:
    return UploadJob.objects.filter(
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        source_system="migration",
        source_file_persisted=True,
    ).order_by("id")


def _selected_migration_jobs(limit: int) -> tuple[QuerySet[UploadJob], int]:
    jobs = _pending_migration_jobs()
    total = jobs.count()
    if limit > 0:
        ids = list(jobs.values_list("id", flat=True)[:limit])
        jobs = UploadJob.objects.filter(id__in=ids).order_by("id")
    return jobs, total


def _count_candidates(jobs: QuerySet[UploadJob]) -> MigrationCandidateCounts:
    eligible = 0
    orphaned = 0
    for upload_job_model in jobs.iterator():
        upload_job = cast(MigrationUploadJob, upload_job_model)
        if _source_file_exists(upload_job):
            eligible += 1
        else:
            orphaned += 1
    return MigrationCandidateCounts(eligible=eligible, orphaned=orphaned)


def _apply_migration_job_updates(
    jobs: QuerySet[UploadJob],
    *,
    now: datetime,
) -> MigrationUpdateCounts:
    results = [
        _apply_migration_job_update(str(pk), now=now)
        for pk in jobs.values_list("pk", flat=True)
    ]
    return MigrationUpdateCounts(
        updated_eligible=sum(1 for result in results if result.eligible),
        marked_lost=sum(1 for result in results if result.marked_lost),
        repaired_orphaned=sum(1 for result in results if result.repaired_orphaned),
        skipped_live_lease=sum(1 for result in results if result.skipped_live_lease),
    )


@transaction.atomic
def _apply_migration_job_update(
    upload_job_id: str,
    *,
    now: datetime,
) -> MigrationUpdateResult:
    locked_job = (
        _pending_migration_jobs().select_for_update().filter(pk=upload_job_id).first()
    )
    if locked_job is None:
        # Selection is a snapshot; cleanup may have changed or removed this row.
        return MigrationUpdateResult(False, False, False)
    if _has_live_processing_lease(locked_job, now=now):
        # A running import owns this row; marking it LOST would race its owner.
        return MigrationUpdateResult(False, False, False, skipped_live_lease=True)
    upload_job = cast(MigrationUploadJob, locked_job)
    source_exists = _source_file_exists(upload_job)
    update_fields, marked_lost = _prepare_cleanup_state(upload_job, now=now)
    if marked_lost:
        mark_upload_job_integrity_lost(
            locked_job,
            locked_job.error_detail or _lost_detail(source_exists=source_exists),
            error_code=(
                UploadJob.ErrorCode.PROCESSING_FAILED.value
                if source_exists
                else UploadJob.ErrorCode.SOURCE_MISSING.value
            ),
        )
    if source_exists:
        _mark_source_cleanup_eligible(upload_job, update_fields=update_fields)
        return MigrationUpdateResult(True, marked_lost, False)
    _repair_orphaned_source(upload_job, update_fields=update_fields)
    return MigrationUpdateResult(False, marked_lost, True)


def _prepare_cleanup_state(
    upload_job: MigrationUploadJob,
    *,
    now: datetime,
) -> tuple[list[str], bool]:
    update_fields = ["cleanup_status", "updated_at"]
    if upload_job.source_file_delete_eligible_at is None:
        upload_job.source_file_delete_eligible_at = now
        update_fields.append("source_file_delete_eligible_at")
    marked_lost = upload_job.status in {
        UploadJob.Status.PENDING.value,
        UploadJob.Status.PROCESSING.value,
    }
    return update_fields, marked_lost


def _has_live_processing_lease(upload_job: UploadJob, *, now: datetime) -> bool:
    expires_at = upload_job.processing_lease_expires_at
    return bool(upload_job.processing_lease_owner) and (
        expires_at is not None and expires_at > now
    )


def _lost_detail(*, source_exists: bool) -> str:
    if source_exists:
        return (
            "Migration-created upload job abandoned during data recovery; "
            "source artifact is eligible for cleanup."
        )
    return "Migration source file missing during cleanup eligibility backfill."


def _mark_source_cleanup_eligible(
    upload_job: MigrationUploadJob,
    *,
    update_fields: list[str],
) -> None:
    upload_job.cleanup_status = UploadJob.CleanupStatus.ELIGIBLE.value
    upload_job.save(update_fields=update_fields)


def _repair_orphaned_source(
    upload_job: MigrationUploadJob,
    *,
    update_fields: list[str],
) -> None:
    orphan_update_fields = [
        "file",
        "source_file_persisted",
        "cleanup_status",
        "updated_at",
    ]
    if "source_file_delete_eligible_at" in update_fields:
        orphan_update_fields.append("source_file_delete_eligible_at")
    upload_job.file.name = ""
    upload_job.source_file_persisted = False
    upload_job.cleanup_status = UploadJob.CleanupStatus.COMPLETED.value
    upload_job.save(update_fields=orphan_update_fields)


def _source_file_exists(upload_job: MigrationUploadJob) -> bool:
    file_name = upload_job.file.name.strip()
    if not file_name:
        return False
    storage = upload_job.file.storage
    exists = getattr(storage, "exists", None)
    if not callable(exists):
        return False
    return bool(exists(file_name))
