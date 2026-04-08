from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import UploadJob
from endoreg_db.services.hub.cleanup import reap_upload_job_sources


class HubCleanupTests(TestCase):
    def test_reap_upload_job_sources_deletes_existing_file_and_marks_completed(self):
        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="cleanup.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            cleanup_status=UploadJob.CleanupStatus.ELIGIBLE,
            source_file_persisted=True,
        )

        cleaned = reap_upload_job_sources()

        upload_job.refresh_from_db()
        assert cleaned == 1
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
        assert upload_job.source_file_persisted is False
        assert upload_job.file.name == ""

    def test_reap_upload_job_sources_is_idempotent_when_file_is_already_missing(self):
        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="cleanup-missing.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            cleanup_status=UploadJob.CleanupStatus.ELIGIBLE,
            source_file_persisted=True,
        )
        upload_job.file.delete(save=False)
        upload_job.save(update_fields=["file"])

        cleaned = reap_upload_job_sources()

        upload_job.refresh_from_db()
        assert cleaned == 1
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
        assert upload_job.source_file_persisted is False
        assert upload_job.file.name == ""
