from __future__ import annotations

import json
import pytest
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from endoreg_db.models import UploadJob
from endoreg_db.management.commands.migration_mark_eligible import (
    MigrationCandidateCounts,
)
from endoreg_db.services.hub.upload_job_state_machine import (
    mark_upload_job_integrity_lost,
)


class MigrationMarkEligibleCommandTests(TestCase):
    def _make_job(self) -> UploadJob:
        return UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="migration-cleanup.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            source_system="migration",
            source_file_persisted=True,
            cleanup_status=UploadJob.CleanupStatus.PENDING,
            status=UploadJob.Status.PENDING,
        )

    def test_dry_run_reports_counts_without_modifying_rows(self) -> None:
        self._make_job()

        output = StringIO()
        call_command("migration_mark_eligible", stdout=output)

        upload_job = UploadJob.objects.get()
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.PENDING)
        self.assertIn(
            "Eligible candidates: 1; orphaned candidates: 0", output.getvalue()
        )

    def test_dry_run_json_summary_is_machine_readable(self) -> None:
        self._make_job()

        output = StringIO()
        call_command("migration_mark_eligible", "--json", stdout=output)

        json_line = next(
            line for line in output.getvalue().splitlines() if line.startswith("{")
        )
        payload = json.loads(json_line)
        self.assertEqual(payload["eligible_candidates"], 1)
        self.assertEqual(payload["orphaned_candidates"], 0)
        self.assertTrue(payload["dry_run"])

    def test_apply_marks_existing_source_as_eligible(self) -> None:
        upload_job = self._make_job()

        call_command("migration_mark_eligible", "--apply")

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.ELIGIBLE)
        self.assertTrue(upload_job.source_file_persisted)
        self.assertEqual(upload_job.status, UploadJob.Status.LOST)
        self.assertEqual(
            upload_job.error_code,
            UploadJob.ErrorCode.PROCESSING_FAILED,
        )
        self.assertIsNotNone(upload_job.source_file_delete_eligible_at)
        self.assertIn("abandoned during data recovery", upload_job.error_detail)

    def test_apply_is_idempotent_after_marking_eligible(self) -> None:
        upload_job = self._make_job()

        call_command("migration_mark_eligible", "--apply")
        upload_job.refresh_from_db()
        first_error_detail = upload_job.error_detail
        first_eligible_at = upload_job.source_file_delete_eligible_at

        output = StringIO()
        call_command("migration_mark_eligible", "--apply", "--json", stdout=output)

        upload_job.refresh_from_db()
        json_line = next(
            line for line in output.getvalue().splitlines() if line.startswith("{")
        )
        payload = json.loads(json_line)
        self.assertEqual(payload["selected_rows"], 0)
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.ELIGIBLE)
        self.assertEqual(upload_job.status, UploadJob.Status.LOST)
        self.assertEqual(
            upload_job.error_code,
            UploadJob.ErrorCode.PROCESSING_FAILED,
        )
        self.assertEqual(upload_job.error_detail, first_error_detail)
        self.assertEqual(upload_job.source_file_delete_eligible_at, first_eligible_at)

    def test_apply_repairs_orphaned_source_metadata_and_marks_job_lost(self) -> None:
        upload_job = self._make_job()
        upload_job.file.delete(save=False)
        upload_job.save(update_fields=["file"])

        call_command("migration_mark_eligible", "--apply")

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.COMPLETED)
        self.assertFalse(upload_job.source_file_persisted)
        self.assertEqual(upload_job.file.name, "")
        self.assertEqual(upload_job.status, UploadJob.Status.LOST)
        self.assertEqual(upload_job.error_code, UploadJob.ErrorCode.SOURCE_MISSING)
        self.assertIsNotNone(upload_job.source_file_delete_eligible_at)
        self.assertIn("Migration source file missing", upload_job.error_detail)

    def test_apply_leaves_job_with_live_processing_lease_unchanged(self) -> None:
        upload_job = self._make_job()
        upload_job.status = UploadJob.Status.PROCESSING
        upload_job.processing_lease_owner = "live-import-worker"
        upload_job.processing_lease_expires_at = timezone.now() + timedelta(minutes=5)
        upload_job.processing_heartbeat_at = timezone.now()
        upload_job.save(
            update_fields=[
                "status",
                "processing_lease_owner",
                "processing_lease_expires_at",
                "processing_heartbeat_at",
            ]
        )

        output = StringIO()
        call_command("migration_mark_eligible", "--apply", "--json", stdout=output)

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.status, UploadJob.Status.PROCESSING)
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.PENDING)
        self.assertEqual(upload_job.processing_lease_owner, "live-import-worker")
        self.assertIsNone(upload_job.source_file_delete_eligible_at)
        json_line = next(
            line for line in output.getvalue().splitlines() if line.startswith("{")
        )
        payload = json.loads(json_line)
        self.assertEqual(payload["skipped_live_lease"], 1)
        self.assertEqual(payload["marked_lost"], 0)

    def test_apply_marks_expired_processing_lease_lost_through_state_machine(
        self,
    ) -> None:
        upload_job = self._make_job()
        upload_job.status = UploadJob.Status.PROCESSING
        upload_job.processing_lease_owner = "expired-import-worker"
        upload_job.processing_lease_expires_at = timezone.now() - timedelta(minutes=5)
        upload_job.processing_heartbeat_at = timezone.now() - timedelta(minutes=10)
        upload_job.save(
            update_fields=[
                "status",
                "processing_lease_owner",
                "processing_lease_expires_at",
                "processing_heartbeat_at",
            ]
        )

        with self.assertLogs("endoreg_db.models.hub.upload_job", level="ERROR") as logs:
            call_command("migration_mark_eligible", "--apply")

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.status, UploadJob.Status.LOST)
        self.assertEqual(upload_job.error_code, UploadJob.ErrorCode.PROCESSING_FAILED)
        self.assertFalse(upload_job.retryable)
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.ELIGIBLE)
        self.assertTrue(any("media.integrity_lost" in line for line in logs.output))

    def test_apply_preserves_existing_error_detail_for_already_lost_job(self) -> None:
        upload_job = self._make_job()
        mark_upload_job_integrity_lost(
            upload_job,
            "Operator note: decoder stalled.",
            error_code=UploadJob.ErrorCode.PROCESSING_FAILED.value,
        )

        call_command("migration_mark_eligible", "--apply")

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.status, UploadJob.Status.LOST)
        self.assertEqual(upload_job.error_detail, "Operator note: decoder stalled.")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change", ["cleanup_completed", "source_removed", "source_reassigned", "deleted"]
)
def test_selected_job_is_rechecked_before_cleanup_mutation(change: str) -> None:
    job = UploadJob.objects.create(
        source_system="migration",
        source_file_persisted=True,
    )
    selected_id = str(job.pk)

    def change_selected_job(_jobs: object) -> MigrationCandidateCounts:
        if change == "deleted":
            job.delete()
        elif change == "cleanup_completed":
            job.cleanup_status = UploadJob.CleanupStatus.COMPLETED
            job.save(update_fields=["cleanup_status"])
        elif change == "source_removed":
            job.source_file_persisted = False
            job.save(update_fields=["source_file_persisted"])
        else:
            job.source_system = "watcher"
            job.save(update_fields=["source_system"])
        return MigrationCandidateCounts(eligible=0, orphaned=1)

    output = StringIO()
    with patch(
        "endoreg_db.management.commands.migration_mark_eligible._count_candidates",
        side_effect=change_selected_job,
    ):
        call_command(
            "migration_mark_eligible",
            "--apply",
            "--limit",
            "1",
            "--json",
            stdout=output,
        )

    payload = json.loads(
        next(line for line in output.getvalue().splitlines() if line.startswith("{"))
    )
    assert payload["selected_rows"] == 1
    assert payload["updated_eligible"] == 0
    assert payload["marked_lost"] == 0
    assert payload["repaired_orphaned"] == 0
    if change == "deleted":
        assert not UploadJob.objects.filter(pk=selected_id).exists()
    else:
        job.refresh_from_db()
        assert job.status == UploadJob.Status.PENDING
        assert job.source_file_delete_eligible_at is None
        assert job.cleanup_status == (
            UploadJob.CleanupStatus.COMPLETED
            if change == "cleanup_completed"
            else UploadJob.CleanupStatus.PENDING
        )
