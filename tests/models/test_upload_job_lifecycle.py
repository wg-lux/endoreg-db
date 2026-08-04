from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import UploadJob


class UploadJobLifecycleTests(TestCase):
    def _make_job(self, *, retention_policy: str) -> UploadJob:
        return UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="lifecycle.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            retention_policy=retention_policy,
        )

    def test_mark_completed_preserve_source_skips_cleanup(self) -> None:
        upload_job = self._make_job(
            retention_policy=UploadJob.RetentionPolicy.PRESERVE_SOURCE
        )

        upload_job.mark_completed()
        upload_job.refresh_from_db()

        assert upload_job.status == UploadJob.Status.ANONYMIZED
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.SKIPPED
        assert upload_job.source_file_delete_eligible_at is None

    def test_mark_completed_delete_after_success_marks_cleanup_eligible(self) -> None:
        upload_job = self._make_job(
            retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
        )

        upload_job.mark_completed()
        upload_job.refresh_from_db()

        assert upload_job.status == UploadJob.Status.ANONYMIZED
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
        assert upload_job.source_file_delete_eligible_at is not None

    def test_mark_completed_delete_after_success_keeps_existing_eligible_timestamp(
        self,
    ) -> None:
        upload_job = self._make_job(
            retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
        )

        upload_job.mark_completed()
        upload_job.refresh_from_db()
        first_eligible_at = upload_job.source_file_delete_eligible_at

        upload_job.mark_completed()
        upload_job.refresh_from_db()

        assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
        assert upload_job.source_file_delete_eligible_at == first_eligible_at

    def test_mark_completed_migration_managed_skips_cleanup_until_backfill(
        self,
    ) -> None:
        upload_job = self._make_job(
            retention_policy=UploadJob.RetentionPolicy.MIGRATION_MANAGED
        )

        upload_job.mark_completed()
        upload_job.refresh_from_db()

        assert upload_job.status == UploadJob.Status.ANONYMIZED
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.SKIPPED
        assert upload_job.source_file_delete_eligible_at is None

    def test_mark_processing_and_mark_error_persist_state(self) -> None:
        upload_job = self._make_job(
            retention_policy=UploadJob.RetentionPolicy.PRESERVE_SOURCE
        )

        assert upload_job.is_complete is False
        assert upload_job.is_successful is False

        upload_job.mark_processing()
        upload_job.refresh_from_db()

        assert upload_job.status == UploadJob.Status.PROCESSING
        assert upload_job.is_complete is False

        upload_job.mark_error("processor crashed")
        upload_job.refresh_from_db()

        assert upload_job.status == UploadJob.Status.ERROR
        assert upload_job.error_detail == "processor crashed"
        assert upload_job.is_complete is True
        assert upload_job.is_successful is False

    def test_mark_lost_records_unrecoverable_state(self) -> None:
        upload_job = self._make_job(
            retention_policy=UploadJob.RetentionPolicy.PRESERVE_SOURCE
        )

        upload_job.mark_lost("source file disappeared")
        upload_job.refresh_from_db()

        assert upload_job.status == UploadJob.Status.LOST
        assert upload_job.error_detail == "source file disappeared"
        assert upload_job.is_complete is True
        assert upload_job.is_successful is False
