from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence, TypedDict, Unpack, cast

from django.core.management.base import BaseCommand, CommandParser
from django.db.models import QuerySet
from django.utils import timezone
from lx_dtypes.models.contracts.management_command import (
    MigrationMarkEligibleCommandOptionsPayload,
)

from endoreg_db.models.hub.upload_job import UploadJob


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


@dataclass(frozen=True)
class MigrationUpdateCounts:
    updated_eligible: int
    marked_lost: int
    repaired_orphaned: int


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
        self._write_json_payload(payload, enabled=options_payload.json_output)
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated eligible={counts.updated_eligible}; "
                f"marked_lost={counts.marked_lost}; "
                f"repaired_orphaned={counts.repaired_orphaned}."
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


def _selected_migration_jobs(limit: int) -> tuple[QuerySet[UploadJob], int]:
    jobs = UploadJob.objects.filter(
        cleanup_status=UploadJob.CleanupStatus.PENDING,
        source_system="migration",
        source_file_persisted=True,
    ).order_by("id")
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
        _apply_migration_job_update(cast(MigrationUploadJob, upload_job), now=now)
        for upload_job in jobs.iterator()
    ]
    return MigrationUpdateCounts(
        updated_eligible=sum(1 for result in results if result.eligible),
        marked_lost=sum(1 for result in results if result.marked_lost),
        repaired_orphaned=sum(1 for result in results if result.repaired_orphaned),
    )


def _apply_migration_job_update(
    upload_job: MigrationUploadJob,
    *,
    now: datetime,
) -> MigrationUpdateResult:
    source_exists = _source_file_exists(upload_job)
    update_fields, marked_lost = _prepare_cleanup_state(
        upload_job,
        source_exists=source_exists,
        now=now,
    )
    if source_exists:
        _mark_source_cleanup_eligible(upload_job, update_fields=update_fields)
        return MigrationUpdateResult(True, marked_lost, False)
    _repair_orphaned_source(upload_job, update_fields=update_fields)
    return MigrationUpdateResult(False, marked_lost, True)


def _prepare_cleanup_state(
    upload_job: MigrationUploadJob,
    *,
    source_exists: bool,
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
    if marked_lost:
        upload_job.status = UploadJob.Status.LOST.value
        upload_job.error_code = (
            UploadJob.ErrorCode.PROCESSING_FAILED.value
            if source_exists
            else UploadJob.ErrorCode.SOURCE_MISSING.value
        )
        update_fields.extend(["status", "error_code"])
    return update_fields, marked_lost


def _mark_source_cleanup_eligible(
    upload_job: MigrationUploadJob,
    *,
    update_fields: list[str],
) -> None:
    if upload_job.status == UploadJob.Status.LOST.value and not upload_job.error_detail:
        upload_job.error_detail = (
            "Migration-created upload job abandoned during data recovery; "
            "source artifact is eligible for cleanup."
        )
        update_fields.append("error_detail")
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
    if upload_job.status == UploadJob.Status.LOST.value:
        orphan_update_fields.extend(["status", "error_code", "error_detail"])
        if not upload_job.error_detail:
            upload_job.error_detail = (
                "Migration source file missing during cleanup eligibility backfill."
            )
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
