from __future__ import annotations

from uuid import uuid4
from pathlib import Path

from django.test import TestCase
from django.core.files.base import ContentFile

from endoreg_db.models import Center, RawPdfFile, UploadJob, VideoFile
from endoreg_db.services.media.operation_gate import create_video_stream_lease
from endoreg_db.utils.file_operations import atomic_write_file
from endoreg_db.utils.hashs import get_file_hash
from endoreg_db.utils.paths import get_runtime_paths


class MediaManagementEndpointTests(TestCase):
    def setUp(self):
        suffix = uuid4().hex[:8]
        self.center = Center.objects.create(name=f"mm-center-{suffix}")
        self.video = VideoFile.objects.create(
            center=self.center,
            raw_video_hash=f"mm-video-{uuid4().hex}",
            original_file_name="mm-video.mp4",
        )

    def test_media_management_status_endpoint(self):
        response = self.client.get("/api/media-management/status/")
        assert response.status_code == 200, response.content
        payload = response.json()
        assert "videos" in payload
        assert "pdfs" in payload
        assert "cleanup_opportunities" in payload
        assert "total_files" in payload

    def test_media_management_cleanup_dry_run_endpoint(self):
        response = self.client.delete(
            "/api/media-management/cleanup/?type=unfinished&force=false"
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert "summary" in payload
        assert payload["summary"]["dry_run"] is True

    def test_media_management_force_remove_missing_file(self):
        response = self.client.delete(
            "/api/media-management/force-remove/video/999999/"
        )
        assert response.status_code == 404, response.content
        assert response.json()["detail"] == "File not found"

    def test_untyped_and_invalid_deletion_preserve_both_media(self):
        pdf = RawPdfFile.objects.create(
            pk=self.video.pk, center=self.center, pdf_hash=uuid4().hex
        )
        for prefix in ("", "unknown/", "all/"):
            response = self.client.delete(
                f"/api/media-management/force-remove/{prefix}{self.video.pk}/"
            )
            assert response.status_code == 400, response.content
            assert VideoFile.objects.filter(pk=self.video.pk).exists()
            assert RawPdfFile.objects.filter(pk=pdf.pk).exists()

    def test_typed_deletion_preserves_other_type_and_job_history(self):
        pdf = RawPdfFile.objects.create(
            pk=self.video.pk, center=self.center, pdf_hash=uuid4().hex
        )
        media_id = self.video.pk
        video_hash = self.video.raw_video_hash
        job = UploadJob.objects.create(
            source_center=self.center, content_type="video/mp4", content_hash=video_hash
        )
        previous_job = UploadJob.objects.create(
            source_center=self.center,
            content_type="video/mp4",
            content_hash=video_hash,
            status=UploadJob.Status.ERROR,
            error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        )
        response = self.client.delete(
            f"/api/media-management/force-remove/pdf/{media_id}/"
        )
        assert response.status_code == 200, response.content
        assert response.json()["file_type"] == "pdf"
        assert not RawPdfFile.objects.filter(pk=pdf.pk).exists()
        assert VideoFile.objects.filter(pk=media_id).exists()
        job.refresh_from_db()
        assert job.status == UploadJob.Status.PENDING
        # A missing PDF must never fall through to the video with the same ID.
        response = self.client.delete(
            f"/api/media-management/force-remove/pdf/{media_id}/"
        )
        assert response.status_code == 404, response.content
        assert VideoFile.objects.filter(pk=media_id).exists()
        response = self.client.delete(
            f"/api/media-management/force-remove/video/{media_id}/"
        )
        assert response.status_code == 409, response.content
        assert VideoFile.objects.filter(pk=media_id).exists()
        job.refresh_from_db()
        assert job.status == UploadJob.Status.PENDING

        # Explicit deletion may proceed only once the import is terminal.
        job.status = UploadJob.Status.ANONYMIZED
        job.save(update_fields=["status", "updated_at"])
        response = self.client.delete(
            f"/api/media-management/force-remove/video/{media_id}/"
        )
        assert response.status_code == 200, response.content
        assert response.json()["file_type"] == "video"
        assert not VideoFile.objects.filter(pk=media_id).exists()
        job.refresh_from_db()
        previous_job.refresh_from_db()
        assert job.status == UploadJob.Status.LOST
        assert job.error_code == UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED
        assert (
            job.processing_provenance["media_integrity_status"]
            == "media_record_missing"
        )
        assert previous_job.status == UploadJob.Status.ERROR

    def test_video_deletion_without_job_preserves_pdf(self):
        pdf = RawPdfFile.objects.create(
            pk=self.video.pk, center=self.center, pdf_hash=uuid4().hex
        )
        media_id = self.video.pk
        response = self.client.delete(
            f"/api/media-management/force-remove/video/{media_id}/"
        )
        assert response.status_code == 200, response.content
        assert RawPdfFile.objects.filter(pk=pdf.pk).exists()

        response = self.client.delete(
            f"/api/media-management/force-remove/video/{media_id}/"
        )
        assert response.status_code == 404, response.content
        assert RawPdfFile.objects.filter(pk=pdf.pk).exists()

    def test_video_deletion_defers_during_playback(self):
        lease = create_video_stream_lease(self.video, file_type="processed")
        assert lease is not None
        response = self.client.delete(
            f"/api/media-management/force-remove/video/{self.video.pk}/"
        )
        assert response.status_code == 409, response.content
        assert VideoFile.objects.filter(pk=self.video.pk).exists()

    def test_pdf_deletion_preserves_job_with_integrity_failure(self):
        pdf = RawPdfFile.objects.create(
            pk=self.video.pk, center=self.center, pdf_hash=uuid4().hex
        )
        job = UploadJob.objects.create(
            source_center=self.center,
            content_type="application/pdf",
            content_hash=pdf.pdf_hash,
            status=UploadJob.Status.ANONYMIZED,
        )
        response = self.client.delete(
            f"/api/media-management/force-remove/pdf/{pdf.pk}/"
        )
        assert response.status_code == 200, response.content
        assert VideoFile.objects.filter(pk=self.video.pk).exists()
        job.refresh_from_db()
        assert job.status == UploadJob.Status.LOST
        assert job.error_code == UploadJob.ErrorCode.MEDIA_INTEGRITY_FAILED
        assert job.processing_provenance["media_integrity_missing_artifacts"] == [
            "raw_pdf_file"
        ]

    def test_pdf_force_removal_deletes_recorded_upload_and_drop(self):
        source = get_runtime_paths().import_report / f"{uuid4().hex}.pdf"
        atomic_write_file(destination=source, content=[b"private report"])
        digest = get_file_hash(source)
        pdf = RawPdfFile.objects.create(
            center=self.center,
            pdf_hash=digest,
            file=ContentFile(b"private report", name="raw.pdf"),
            processed_file=ContentFile(b"anonymous report", name="processed.pdf"),
        )
        job = UploadJob.objects.create(
            source_center=self.center,
            content_type="application/pdf",
            content_hash=digest,
            file=ContentFile(b"private report", name="upload.pdf"),
            status=UploadJob.Status.ERROR,
            error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
            source_file_persisted=True,
            processing_provenance={"watched_path": str(source)},
        )
        upload = Path(job.file.path)
        raw_name, processed_name = pdf.file.name, pdf.processed_file.name
        assert raw_name and processed_name
        storage = pdf.file.storage
        response = self.client.delete(
            f"/api/media-management/force-remove/pdf/{pdf.pk}/"
        )
        assert response.status_code == 200, response.content
        assert not source.exists() and not upload.exists()
        assert not storage.exists(raw_name)
        assert not pdf.processed_file.storage.exists(processed_name)
        job.refresh_from_db()
        assert not job.file.name and not job.source_file_persisted
        assert job.cleanup_status == UploadJob.CleanupStatus.COMPLETED

    def test_pdf_force_removal_blocks_active_import(self):
        pdf = RawPdfFile.objects.create(center=self.center, pdf_hash=uuid4().hex)
        UploadJob.objects.create(
            source_center=self.center,
            content_type="application/pdf",
            content_hash=pdf.pdf_hash,
        )
        response = self.client.delete(
            f"/api/media-management/force-remove/pdf/{pdf.pk}/"
        )
        assert response.status_code == 409, response.content
        assert RawPdfFile.objects.filter(pk=pdf.pk).exists()

    def test_media_management_reset_status_for_video(self):
        response = self.client.post(
            f"/api/media-management/reset-status/{self.video.pk}/"
        )
        assert response.status_code == 200, response.content
        payload = response.json()
        assert payload["file_type"] == "video"
        assert payload["file_id"] == self.video.pk
        assert payload["new_status"] == "not_started"
