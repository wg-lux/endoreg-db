from __future__ import annotations

import json
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase

from endoreg_db.models import UploadJob


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
        self.assertIsNotNone(upload_job.source_file_delete_eligible_at)
        self.assertIn("Migration source file missing", upload_job.error_detail)
