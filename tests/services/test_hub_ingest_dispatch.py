from __future__ import annotations

from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import Center, RawPdfFile, UploadJob
from endoreg_db.services.hub.ingest import (
    create_or_reuse_upload_job,
    process_upload_job,
    start_upload_job_processing,
)


class UploadJobDispatchTests(TestCase):
    def setUp(self) -> None:
        self.center = Center.objects.create(
            name="dispatch-center",
            display_name="Dispatch Center",
        )

    def _create_upload_job(self) -> UploadJob:
        return UploadJob.objects.create(
            file=SimpleUploadedFile(
                name="dispatch.pdf",
                content=b"%PDF-1.4\n%%EOF\n",
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            source_center=self.center,
            source_system="api",
            processing_provenance={"entrypoint": "api"},
        )

    def test_start_upload_job_processing_dispatches_inline_when_no_task_dispatcher(
        self,
    ):
        upload_job = self._create_upload_job()
        assert upload_job.storage_class == UploadJob.StorageClass.INGEST
        assert upload_job.storage_tier == UploadJob.StorageTier.UPLOAD_API
        assert upload_job.retention_policy == UploadJob.RetentionPolicy.PRESERVE_SOURCE

        with patch(
            "endoreg_db.services.hub.ingest.process_upload_job",
            return_value=True,
        ) as process_upload_job:
            handoff_mode = start_upload_job_processing(upload_job=upload_job)

        upload_job.refresh_from_db()
        assert handoff_mode == "inline"
        process_upload_job.assert_called_once_with(str(upload_job.id))
        assert upload_job.processing_provenance["processing_handoff"] == "inline"
        assert upload_job.processing_provenance["ingest_mode"] == "api"
        assert (
            upload_job.processing_provenance["source_center_key"]
            == self.center.center_key
        )
        assert (
            upload_job.processing_provenance["retention_policy"]
            == UploadJob.RetentionPolicy.PRESERVE_SOURCE
        )

    def test_start_upload_job_processing_dispatches_to_celery_when_available(self):
        upload_job = self._create_upload_job()
        task_dispatcher = Mock()

        handoff_mode = start_upload_job_processing(
            upload_job=upload_job,
            task_dispatcher=task_dispatcher,
        )

        upload_job.refresh_from_db()
        assert handoff_mode == "celery"
        task_dispatcher.delay.assert_called_once_with(str(upload_job.id))
        assert upload_job.processing_provenance["processing_handoff"] == "celery"

    def test_start_upload_job_processing_marks_job_error_when_dispatch_fails(self):
        upload_job = self._create_upload_job()
        task_dispatcher = Mock()
        task_dispatcher.delay.side_effect = RuntimeError("queue unavailable")

        with self.assertRaises(RuntimeError, msg="queue unavailable"):
            start_upload_job_processing(
                upload_job=upload_job,
                task_dispatcher=task_dispatcher,
            )

        upload_job.refresh_from_db()
        assert upload_job.status == UploadJob.Status.ERROR
        assert (
            "Failed to start processing: queue unavailable" in upload_job.error_detail
        )

    def test_start_upload_job_processing_raises_when_inline_processing_fails(self):
        upload_job = self._create_upload_job()

        def _mark_error(job_id: str) -> bool:
            assert job_id == str(upload_job.id)
            upload_job.mark_error("inline processing failed")
            return False

        with (
            patch(
                "endoreg_db.services.hub.ingest.process_upload_job",
                side_effect=_mark_error,
            ),
            self.assertRaises(RuntimeError, msg="inline processing failed"),
        ):
            start_upload_job_processing(upload_job=upload_job)

        upload_job.refresh_from_db()
        assert upload_job.status == UploadJob.Status.ERROR
        assert (
            "Failed to start processing: inline processing failed"
            in upload_job.error_detail
        )

    def test_create_or_reuse_upload_job_normalizes_provenance_contract(self):
        with patch("endoreg_db.services.hub.audit.logger.info") as audit_log:
            upload_job, created = create_or_reuse_upload_job(
                uploaded_file=SimpleUploadedFile(
                    name="dispatch.pdf",
                    content=b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                content_type="application/pdf",
                source_center=self.center,
                source_system="site-a",
                processing_provenance={"custom_marker": "present"},
            )

        assert created is True
        assert upload_job.processing_provenance["entrypoint"] == "api"
        assert upload_job.processing_provenance["ingest_mode"] == "api"
        assert upload_job.processing_provenance["source_system"] == "site-a"
        assert (
            upload_job.processing_provenance["content_hash"] == upload_job.content_hash
        )
        assert (
            upload_job.processing_provenance["source_center_key"]
            == self.center.center_key
        )
        assert (
            upload_job.processing_provenance["storage_tier"]
            == UploadJob.StorageTier.UPLOAD_API
        )
        assert (
            upload_job.processing_provenance["retention_policy"]
            == UploadJob.RetentionPolicy.PRESERVE_SOURCE
        )
        assert upload_job.processing_provenance["custom_marker"] == "present"
        audit_log.assert_called()
        assert "hub.upload_job_created" in audit_log.call_args.args[0]

    def test_process_upload_job_preserve_source_keeps_cleanup_skipped(self):
        upload_job = self._create_upload_job()
        report = RawPdfFile(center=self.center)

        with patch(
            "endoreg_db.services.hub.ingest.ReportImportService.import_and_anonymize",
            return_value=report,
        ):
            processed = process_upload_job(str(upload_job.id))

        upload_job.refresh_from_db()
        assert processed is True
        assert upload_job.status == UploadJob.Status.ANONYMIZED
        assert upload_job.cleanup_status == UploadJob.CleanupStatus.SKIPPED
        assert upload_job.source_file_delete_eligible_at is None
        assert (
            upload_job.processing_provenance["stored_upload_path"]
            == upload_job.file.name
        )
