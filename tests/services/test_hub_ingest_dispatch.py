from __future__ import annotations

from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from endoreg_db.models import Center, UploadJob
from endoreg_db.services.hub.ingest import start_upload_job_processing


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
