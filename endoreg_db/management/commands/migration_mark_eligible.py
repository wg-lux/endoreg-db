from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from endoreg_db.models import UploadJob


class Command(BaseCommand):
    help = (
        "Mark migration-created UploadJob source files as cleanup-eligible and "
        "repair orphaned source metadata."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON summary.",
        )

    def handle(self, *args, **options) -> None:
        qs = UploadJob.objects.filter(
            cleanup_status=UploadJob.CleanupStatus.PENDING,
            source_system="migration",
            source_file_persisted=True,
        ).order_by("id")

        total = qs.count()
        limit = int(options["limit"] or 0)

        if limit > 0:
            ids = list(qs.values_list("id", flat=True)[:limit])
            qs = UploadJob.objects.filter(id__in=ids).order_by("id")

        selected = qs.count()
        payload: dict[str, object] = {
            "total_pending_migration_rows": total,
            "selected_rows": selected,
            "dry_run": not bool(options["apply"]),
            "applied": bool(options["apply"]),
        }

        self.stdout.write(f"Matching migration UploadJobs: {selected} / total {total}")

        eligible_candidates = 0
        orphaned_candidates = 0
        for upload_job in qs.iterator():
            source_exists = _source_file_exists(upload_job)
            if source_exists:
                eligible_candidates += 1
            else:
                orphaned_candidates += 1
        self.stdout.write(
            f"Eligible candidates: {eligible_candidates}; "
            f"orphaned candidates: {orphaned_candidates}"
        )
        payload["eligible_candidates"] = eligible_candidates
        payload["orphaned_candidates"] = orphaned_candidates

        if not options["apply"]:
            if options["json"]:
                self.stdout.write(json.dumps(payload, sort_keys=True))
            self.stdout.write("Dry run only. Re-run with --apply.")
            return

        updated_eligible = 0
        marked_lost = 0
        repaired_orphaned = 0
        now = timezone.now()
        for upload_job in qs.iterator():
            source_exists = _source_file_exists(upload_job)
            update_fields = [
                "cleanup_status",
                "updated_at",
            ]
            if upload_job.source_file_delete_eligible_at is None:
                upload_job.source_file_delete_eligible_at = now
                update_fields.append("source_file_delete_eligible_at")

            if upload_job.status in {
                UploadJob.Status.PENDING,
                UploadJob.Status.PROCESSING,
            }:
                upload_job.status = UploadJob.Status.LOST
                update_fields.append("status")
                marked_lost += 1

            if source_exists:
                if (
                    upload_job.status == UploadJob.Status.LOST
                    and not upload_job.error_detail
                ):
                    upload_job.error_detail = (
                        "Migration-created upload job abandoned during data recovery; "
                        "source artifact is eligible for cleanup."
                    )
                    update_fields.append("error_detail")
                upload_job.cleanup_status = UploadJob.CleanupStatus.ELIGIBLE
                upload_job.save(update_fields=update_fields)
                updated_eligible += 1
                continue

            orphan_update_fields = [
                "file",
                "source_file_persisted",
                "cleanup_status",
                "updated_at",
            ]
            if "source_file_delete_eligible_at" in update_fields:
                orphan_update_fields.append("source_file_delete_eligible_at")
            if upload_job.status == UploadJob.Status.LOST:
                orphan_update_fields.extend(["status", "error_detail"])
                if not upload_job.error_detail:
                    upload_job.error_detail = "Migration source file missing during cleanup eligibility backfill."
            upload_job.file.name = ""
            upload_job.source_file_persisted = False
            upload_job.cleanup_status = UploadJob.CleanupStatus.COMPLETED
            upload_job.save(update_fields=orphan_update_fields)
            repaired_orphaned += 1
        payload["updated_eligible"] = updated_eligible
        payload["marked_lost"] = marked_lost
        payload["repaired_orphaned"] = repaired_orphaned

        if options["json"]:
            self.stdout.write(json.dumps(payload, sort_keys=True))
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated eligible={updated_eligible}; marked_lost={marked_lost}; "
                f"repaired_orphaned={repaired_orphaned}."
            )
        )


def _source_file_exists(upload_job: UploadJob) -> bool:
    file_name = str(getattr(upload_job.file, "name", "") or "").strip()
    if not file_name:
        return False
    return bool(upload_job.file.storage.exists(file_name))
