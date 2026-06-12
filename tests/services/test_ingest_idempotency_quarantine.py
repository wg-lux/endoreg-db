from __future__ import annotations

# pyright: reportUnknownVariableType=false

import threading
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from typing import Any, cast

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from endoreg_db.models import (
    Center,
    EndoscopyProcessor,
    RawPdfFile,
    UploadJob,
)
from endoreg_db.services.hub.ingest import (
    _run_video_upload_import_job,  # pyright: ignore[reportPrivateUsage]
    create_or_reuse_upload_job,
    process_preanonymized_watcher_file,
    process_watcher_file,
)
from endoreg_db.utils.file_operations import (
    ensure_directory,
    safe_rmtree,
    safe_unlink_file,
)


@override_settings(MEDIA_ROOT=(Path(__file__).parent / "test_media").as_posix())
class IngestIdempotencyQuarantineTests(TransactionTestCase):
    test_media_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.test_media_dir = Path(__file__).parent / "test_media"
        cls.test_media_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        import shutil

        if cls.test_media_dir.exists():
            shutil.rmtree(cls.test_media_dir)

    def setUp(self) -> None:
        super().setUp()
        self.center = Center.objects.create(
            name="test-center",
            display_name="Test Center",
        )
        self.pdf_content = b"%PDF-1.4\n%%EOF\n"
        self.video_content = (
            b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x00"
        )
        self.quarantine_dir = self.test_media_dir / "quarantine"
        self.quarantine_patch = patch(
            "endoreg_db.services.hub.ingest._quarantine_dir",
            return_value=self.quarantine_dir,
        )
        self.quarantine_patch.start()
        self.addCleanup(self.quarantine_patch.stop)

        if self.quarantine_dir.exists():
            safe_rmtree(self.quarantine_dir)
        ensure_directory(self.quarantine_dir)

    def tearDown(self) -> None:
        super().tearDown()
        if self.quarantine_dir.exists():
            for item in self.quarantine_dir.iterdir():
                if item.is_file():
                    safe_unlink_file(item)

    def _create_temp_file(self, filename: str, content: bytes) -> Path:
        temp_path = self.test_media_dir / filename
        temp_path.write_bytes(content)
        return temp_path

    def test_create_or_reuse_upload_job_idempotency_with_hash(self):
        filename = "unique_file_by_hash.pdf"
        uploaded_file = SimpleUploadedFile(
            name=filename,
            content=self.pdf_content,
            content_type="application/pdf",
        )

        job1, created1 = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
        )
        self.assertTrue(created1)
        self.assertEqual(UploadJob.objects.count(), 1)

        uploaded_file.seek(0)
        job2, created2 = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
        )
        self.assertFalse(created2)
        self.assertEqual(job1.id, job2.id)
        self.assertEqual(UploadJob.objects.count(), 1)

    def test_create_or_reuse_upload_job_idempotency_with_idempotency_key(self):
        filename = "unique_file_by_key.pdf"
        uploaded_file = SimpleUploadedFile(
            name=filename,
            content=self.pdf_content,
            content_type="application/pdf",
        )
        idempotency_key = "my-unique-key"

        job1, created1 = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
            idempotency_key=idempotency_key,
        )
        self.assertTrue(created1)
        self.assertEqual(UploadJob.objects.count(), 1)

        uploaded_file.seek(0)
        job2, created2 = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
            idempotency_key=idempotency_key,
        )
        self.assertFalse(created2)
        self.assertEqual(job1.id, job2.id)
        self.assertEqual(UploadJob.objects.count(), 1)

    def test_create_or_reuse_upload_job_handles_orphaned_job_by_hash(self):
        filename = "orphaned_by_hash.pdf"
        uploaded_file = SimpleUploadedFile(
            name=filename,
            content=self.pdf_content,
            content_type="application/pdf",
        )

        job, created = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
        )
        self.assertTrue(created)
        self.assertEqual(job.status, UploadJob.Status.PENDING)

        RawPdfFile.objects.create(
            pdf_hash=job.content_hash,
            center=self.center,
            file="path/to/pdf.pdf",
            processed_file="path/to/pdf.pdf",
        )
        job.status = UploadJob.Status.ANONYMIZED
        job.save()

        RawPdfFile.objects.filter(pdf_hash=job.content_hash).delete()
        self.assertFalse(RawPdfFile.objects.filter(pdf_hash=job.content_hash).exists())

        uploaded_file.seek(0)
        job_reingest, created_reingest = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
        )
        self.assertTrue(created_reingest)
        self.assertNotEqual(job.id, job_reingest.id)
        self.assertEqual(UploadJob.objects.count(), 2)
        job.refresh_from_db()
        self.assertEqual(job.status, UploadJob.Status.LOST)
        self.assertIn("media integrity check", job.error_detail)
        self.assertEqual(
            job.processing_provenance["media_integrity_status"],
            "media_record_missing",
        )
        self.assertEqual(job_reingest.status, UploadJob.Status.PENDING)

    def test_create_or_reuse_upload_job_handles_orphaned_job_by_idempotency_key(self):
        filename = "orphaned_by_key.pdf"
        uploaded_file = SimpleUploadedFile(
            name=filename,
            content=self.pdf_content,
            content_type="application/pdf",
        )
        idempotency_key = "orphan-key"

        job, created = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
            idempotency_key=idempotency_key,
        )
        self.assertTrue(created)
        self.assertEqual(job.status, UploadJob.Status.PENDING)

        RawPdfFile.objects.create(
            pdf_hash=job.content_hash,
            center=self.center,
            file="path/to/pdf_orphaned.pdf",
            processed_file="path/to/pdf_orphaned.pdf",
        )
        job.status = UploadJob.Status.ANONYMIZED
        job.save()

        RawPdfFile.objects.filter(pdf_hash=job.content_hash).delete()

        uploaded_file.seek(0)
        job_reingest, created_reingest = create_or_reuse_upload_job(
            uploaded_file=uploaded_file,
            content_type="application/pdf",
            source_center=self.center,
            source_system="test",
            idempotency_key=idempotency_key,
        )
        self.assertTrue(created_reingest)
        self.assertNotEqual(job.id, job_reingest.id)
        self.assertEqual(UploadJob.objects.count(), 2)
        job.refresh_from_db()
        self.assertEqual(job.status, UploadJob.Status.LOST)
        self.assertIn("media integrity check", job.error_detail)
        self.assertEqual(
            job.processing_provenance["media_integrity_status"],
            "media_record_missing",
        )
        self.assertEqual(job_reingest.status, UploadJob.Status.PENDING)

    def test_process_upload_job_quarantines_video_on_failure(self):
        filename = "failed_upload.mp4"
        temp_file_path = self._create_temp_file(filename, self.pdf_content)

        upload_job = UploadJob.objects.create(
            file=SimpleUploadedFile(
                name=filename,
                content=self.pdf_content,
                content_type="video/mp4",
            ),
            content_type="video/mp4",
            source_center=self.center,
            source_system="test",
        )
        upload_job.file.name = temp_file_path.relative_to(
            self.test_media_dir
        ).as_posix()
        upload_job.save()

        with (
            patch(
                "endoreg_db.services.hub.ingest._default_processor_name",
                return_value="processor",
            ),
            patch(
                "endoreg_db.services.hub.ingest.VideoImportService.import_and_anonymize",
                side_effect=ValueError("Test processing error"),
            ),
        ):
            result = _run_video_upload_import_job(str(upload_job.id))

        self.assertFalse(result)

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.status, UploadJob.Status.ERROR)
        self.assertIn("Test processing error", upload_job.error_detail)

        quarantined_path = self.quarantine_dir / filename
        self.assertTrue(quarantined_path.exists())
        self.assertFalse(temp_file_path.exists())
        self.assertEqual(quarantined_path.read_bytes(), self.pdf_content)
        self.assertIn("quarantined_path", upload_job.processing_provenance)
        self.assertEqual(
            upload_job.processing_provenance["quarantined_path"],
            str(quarantined_path),
        )

    def test_process_watcher_file_queues_processing_and_removes_watched_source(self):
        filename = "successful_watcher_report.pdf"
        temp_file_path = self._create_temp_file(filename, self.pdf_content)

        with patch(
            "endoreg_db.tasks.process_upload_job.apply_async",
            return_value=Mock(id="watcher-upload-task"),
        ):
            upload_job = process_watcher_file(
                file_path=temp_file_path,
                file_type="report",
                center=self.center,
            )

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.status, UploadJob.Status.PROCESSING)
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.PENDING)
        self.assertTrue(upload_job.source_file_persisted)
        self.assertFalse(temp_file_path.exists())

    @override_settings(WATCHER_CELERY_INLINE_FALLBACK_ENABLED=True)
    def test_process_watcher_file_uses_explicit_inline_fallback_for_pdf_broker_error(
        self,
    ):
        filename = "fallback_watcher_report.pdf"
        temp_file_path = self._create_temp_file(filename, self.pdf_content)

        def _inline_fallback(**kwargs: Any) -> UploadJob:
            upload_job = cast(UploadJob, kwargs["upload_job"])
            watched_path = cast(Path, kwargs["watched_path"])
            upload_job.mark_completed()
            safe_unlink_file(watched_path, missing_ok=True)
            return upload_job

        with (
            patch(
                "endoreg_db.tasks.process_upload_job.apply_async",
                side_effect=ConnectionRefusedError("broker down"),
            ),
            patch(
                "endoreg_db.services.hub.ingest._run_watcher_upload_job_inline",
                side_effect=_inline_fallback,
            ) as inline_fallback,
        ):
            upload_job = process_watcher_file(
                file_path=temp_file_path,
                file_type="report",
                center=self.center,
            )

        upload_job.refresh_from_db()
        self.assertEqual(upload_job.status, UploadJob.Status.ANONYMIZED)
        inline_fallback.assert_called_once()
        self.assertEqual(inline_fallback.call_args.kwargs["normalized_type"], "report")

    @override_settings(WATCHER_CELERY_INLINE_FALLBACK_ENABLED=False)
    def test_process_watcher_file_fails_closed_on_pdf_broker_error(self):
        filename = "fail_closed_watcher_report.pdf"
        temp_file_path = self._create_temp_file(filename, self.pdf_content)

        with (
            patch(
                "endoreg_db.tasks.process_upload_job.apply_async",
                side_effect=ConnectionRefusedError("broker down"),
            ),
            patch(
                "endoreg_db.services.hub.ingest._run_watcher_upload_job_inline",
                side_effect=AssertionError("inline fallback must be disabled"),
            ),
            self.assertRaises(ConnectionRefusedError),
        ):
            process_watcher_file(
                file_path=temp_file_path,
                file_type="report",
                center=self.center,
            )

        upload_job = UploadJob.objects.order_by("-created_at").first()
        self.assertIsNotNone(upload_job)
        assert upload_job is not None
        self.assertEqual(upload_job.status, UploadJob.Status.ERROR)
        self.assertIn("broker down", upload_job.error_detail)
        quarantined_path = self.quarantine_dir / filename
        self.assertTrue(quarantined_path.exists())
        self.assertFalse(temp_file_path.exists())

    def test_process_watcher_file_quarantines_on_failure(self):
        filename = "failed_watcher_video.mp4"
        temp_file_path = self._create_temp_file(filename, self.video_content)
        EndoscopyProcessor.objects.create(name="test_processor")

        with (
            patch(
                "endoreg_db.tasks.process_upload_job.apply_async",
                side_effect=ValueError("Watcher dispatch error"),
            ),
            patch(
                "endoreg_db.services.hub.ingest.sha256_file",
                return_value="dummy_hash",
            ),
            patch(
                "endoreg_db.services.hub.ingest.File",
                return_value=SimpleUploadedFile(
                    name=filename,
                    content=self.video_content,
                    content_type="video/mp4",
                ),
            ),
            self.assertRaises(ValueError),
        ):
            process_watcher_file(
                file_path=temp_file_path,
                file_type="video",
                center=self.center,
            )

        upload_job = UploadJob.objects.order_by("-created_at").first()
        self.assertIsNotNone(upload_job)
        assert upload_job is not None
        self.assertEqual(upload_job.status, UploadJob.Status.ERROR)
        self.assertIn("Watcher dispatch error", upload_job.error_detail)

        quarantined_path = self.quarantine_dir / filename
        self.assertTrue(quarantined_path.exists())
        self.assertFalse(temp_file_path.exists())
        self.assertEqual(quarantined_path.read_bytes(), self.video_content)
        self.assertIn("quarantined_path", upload_job.processing_provenance)
        self.assertEqual(
            upload_job.processing_provenance["quarantined_path"],
            str(quarantined_path),
        )
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.COMPLETED)
        self.assertFalse(upload_job.source_file_persisted)
        self.assertEqual(upload_job.file.name, "")

    def test_process_preanonymized_watcher_file_quarantines_on_failure(self):
        filename = "failed_preanonymized.mp4"
        sidecar_filename = "failed_preanonymized.json"
        video_path = self._create_temp_file(filename, self.video_content)
        sidecar_path = self._create_temp_file(
            sidecar_filename,
            b'{"patient_hash": "test"}',
        )

        with (
            patch(
                "endoreg_db.services.hub.ingest._finalize_preanonymized_video",
                side_effect=ValueError("Preanonymized processing error"),
            ),
            patch(
                "endoreg_db.services.hub.ingest.sha256_file",
                return_value="dummy_hash_preanonymized",
            ),
            patch(
                "endoreg_db.services.hub.ingest.File",
                return_value=SimpleUploadedFile(
                    name=filename,
                    content=self.video_content,
                    content_type="video/mp4",
                ),
            ),
            self.assertRaises(ValueError),
        ):
            process_preanonymized_watcher_file(
                file_path=video_path,
                center=self.center,
            )

        upload_job = UploadJob.objects.order_by("-created_at").first()
        self.assertIsNotNone(upload_job)
        assert upload_job is not None
        self.assertEqual(upload_job.status, UploadJob.Status.ERROR)
        self.assertIn("Preanonymized processing error", upload_job.error_detail)

        quarantined_video_path = self.quarantine_dir / filename
        quarantined_sidecar_path = self.quarantine_dir / sidecar_filename

        self.assertTrue(quarantined_video_path.exists())
        self.assertFalse(video_path.exists())
        self.assertEqual(quarantined_video_path.read_bytes(), self.video_content)
        self.assertIn("quarantined_path", upload_job.processing_provenance)
        self.assertEqual(
            upload_job.processing_provenance["quarantined_path"],
            str(quarantined_video_path),
        )

        self.assertTrue(quarantined_sidecar_path.exists())
        self.assertFalse(sidecar_path.exists())
        self.assertEqual(
            quarantined_sidecar_path.read_bytes(),
            b'{"patient_hash": "test"}',
        )
        self.assertIn("quarantined_sidecar_path", upload_job.processing_provenance)
        self.assertEqual(
            upload_job.processing_provenance["quarantined_sidecar_path"],
            str(quarantined_sidecar_path),
        )
        self.assertEqual(upload_job.cleanup_status, UploadJob.CleanupStatus.COMPLETED)
        self.assertFalse(upload_job.source_file_persisted)
        self.assertEqual(upload_job.file.name, "")

    def test_stale_watcher_job_reingest_cleans_old_persisted_upload_source(self):
        filename = "stale_watcher_report.pdf"
        watched_path = self._create_temp_file(filename, self.pdf_content)

        stale_job, created = create_or_reuse_upload_job(
            uploaded_file=SimpleUploadedFile(
                name=filename,
                content=self.pdf_content,
                content_type="application/pdf",
            ),
            content_type="application/pdf",
            source_center=self.center,
            source_system="watcher",
            content_hash="stale-hash",
            idempotency_key="watcher:stale-hash:1:15",
            ingest_mode=UploadJob.IngestMode.WATCHER,
            storage_tier=UploadJob.StorageTier.UPLOAD_WATCHER,
            retention_policy=UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
            processing_provenance={
                "entrypoint": "watcher",
                "watched_path": str(watched_path),
                "file_type": "report",
            },
        )
        self.assertTrue(created)
        stale_job.mark_processing()
        UploadJob.objects.filter(pk=stale_job.pk).update(
            updated_at=timezone.now() - timedelta(hours=3)
        )

        with (
            patch(
                "endoreg_db.services.hub.ingest.sha256_file",
                return_value="stale-hash",
            ),
            patch(
                "endoreg_db.tasks.process_upload_job.apply_async",
                return_value=Mock(id="stale-watcher-task"),
            ),
        ):
            new_job = process_watcher_file(
                file_path=watched_path,
                file_type="report",
                center=self.center,
            )

        stale_job.refresh_from_db()
        new_job.refresh_from_db()
        self.assertNotEqual(new_job.pk, stale_job.pk)
        self.assertEqual(stale_job.status, UploadJob.Status.ERROR)
        self.assertEqual(stale_job.cleanup_status, UploadJob.CleanupStatus.COMPLETED)
        self.assertFalse(stale_job.source_file_persisted)
        self.assertEqual(stale_job.file.name, "")

    def test_create_or_reuse_upload_job_concurrency(self):
        """
        This test only makes sense if the UploadJob model has a real DB uniqueness
        constraint backing the idempotency identity. Without that, select_for_update()
        alone cannot make creation race-safe.
        """
        filename = "concurrent_upload.pdf"
        uploaded_file_content = b"concurrent_pdf_data"

        def create_job_in_thread(
            thread_id: int,
            results_list: list[tuple[uuid.UUID | str, bool]],
        ) -> None:
            thread_uploaded_file = SimpleUploadedFile(
                name=f"{filename}_{thread_id}",
                content=uploaded_file_content,
                content_type="application/pdf",
            )
            try:
                job, created = create_or_reuse_upload_job(
                    uploaded_file=thread_uploaded_file,
                    content_type="application/pdf",
                    source_center=self.center,
                    source_system="concurrent_test",
                    idempotency_key=f"concurrent_key_{filename}",
                )
                results_list.append((job.id, created))
            except IntegrityError:
                results_list.append(("IntegrityError", False))
            except Exception as e:
                results_list.append((f"Error: {e}", False))

        num_threads = 5
        results: list[tuple[uuid.UUID | str, bool]] = []
        threads: list[threading.Thread] = []
        for i in range(num_threads):
            thread = threading.Thread(target=create_job_in_thread, args=(i, results))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        successful_jobs: list[tuple[uuid.UUID, bool]] = [
            (job_id, created)
            for job_id, created in results
            if isinstance(job_id, uuid.UUID)
        ]
        self.assertEqual(
            len(successful_jobs),
            num_threads,
            "All threads should result in a job (either created or reused)",
        )

        created_count = sum(1 for _, created in successful_jobs if created)
        reused_count = sum(1 for _, created in successful_jobs if not created)

        self.assertEqual(created_count, 1)
        self.assertEqual(reused_count, num_threads - 1)

        first_job_id = successful_jobs[0][0]
        for job_id, _ in successful_jobs:
            self.assertEqual(job_id, first_job_id)

        final_job = UploadJob.objects.get(id=first_job_id)
        self.assertEqual(final_job.status, UploadJob.Status.PENDING)
        self.assertEqual(UploadJob.objects.count(), 1)
