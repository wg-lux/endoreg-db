from __future__ import annotations

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import UploadJob
from endoreg_db.services.hub.cleanup import (
    cleanup_upload_job_source,
    reap_upload_job_sources,
)


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

        with (
            self.assertLogs(
                "endoreg_db.utils.file_operations", level="INFO"
            ) as file_logs,
            self.assertLogs("endoreg_db.hub.audit", level="INFO") as audit_logs,
        ):
            cleaned = reap_upload_job_sources()

        upload_job.refresh_from_db()
        assert cleaned == 1
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
        assert upload_job.source_file_persisted is False
        assert upload_job.file.name == ""
        file_events = [json.loads(record.getMessage()) for record in file_logs.records]
        file_event = next(
            event for event in file_events if event.get("operation") == "storage_delete"
        )
        audit_event = json.loads(audit_logs.records[-1].getMessage())
        assert file_event["event"] == "file_operation"
        assert file_event["operation"] == "storage_delete"
        assert file_event["status"] == "ok"
        assert audit_event["event"] == "hub.upload_source_cleanup_completed"
        assert audit_event["upload_job_id"] == str(upload_job.pk)
        assert audit_event["storage_object_deleted"] is True

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

    def test_cleanup_upload_job_source_ignores_non_eligible_jobs(self):
        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="cleanup-pending.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            cleanup_status=UploadJob.CleanupStatus.PENDING,
            source_file_persisted=True,
        )
        original_file_name = upload_job.file.name

        cleaned = cleanup_upload_job_source(upload_job)

        upload_job.refresh_from_db()
        assert cleaned is False
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.PENDING
        assert upload_job.source_file_persisted is True
        assert upload_job.file.name == original_file_name

    def test_cleanup_upload_job_source_ignores_unpersisted_source(self):
        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="cleanup-unpersisted.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            cleanup_status=UploadJob.CleanupStatus.ELIGIBLE,
            source_file_persisted=False,
        )
        original_file_name = upload_job.file.name

        cleaned = cleanup_upload_job_source(upload_job)

        upload_job.refresh_from_db()
        assert cleaned is False
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
        assert upload_job.source_file_persisted is False
        assert upload_job.file.name == original_file_name

    def test_reap_upload_job_sources_respects_limit(self):
        first = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="cleanup-first.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            cleanup_status=UploadJob.CleanupStatus.ELIGIBLE,
            source_file_persisted=True,
        )
        second = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="cleanup-second.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            cleanup_status=UploadJob.CleanupStatus.ELIGIBLE,
            source_file_persisted=True,
        )

        cleaned = reap_upload_job_sources(limit=1)

        first.refresh_from_db()
        second.refresh_from_db()
        assert cleaned == 1
        assert first.cleanup_status == UploadJob.CleanupStatus.COMPLETED
        assert second.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
