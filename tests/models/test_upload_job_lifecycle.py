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
