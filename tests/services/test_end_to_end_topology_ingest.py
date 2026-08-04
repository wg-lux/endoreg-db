from __future__ import annotations

import importlib
import uuid
from pathlib import Path

import pytest

from endoreg_db.models import Center, RawPdfFile, UploadJob
from endoreg_db.services.hub.ingest import process_watcher_file
from endoreg_db.utils.filesystem import paths as paths_module
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.storage import save_local_file


@pytest.mark.django_db
def test_watcher_ingest_uses_protected_runtime_topology_and_reuses_duplicate_content(
    monkeypatch,
):
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"
    data_root_rel = f"data/tests/runtime/{unique_suffix}/public"

    with monkeypatch.context() as scoped:
        scoped.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", protected_root_rel)
        scoped.setenv("STORAGE_DIR", f"{protected_root_rel}/storage")
        scoped.setenv("PROTECTED_MEDIA_ROOT", f"{protected_root_rel}/storage")
        scoped.setenv("DATA_DIR", data_root_rel)

        reloaded_paths = importlib.reload(paths_module)

        try:
            center = Center.objects.create(
                name=f"Topology Center {unique_suffix}",
                display_name="Topology Center",
            )
            first_drop = reloaded_paths.WATCHER_REPORT_DROP_DIR / "case-a.pdf"
            second_drop = reloaded_paths.WATCHER_REPORT_DROP_DIR / "case-b.pdf"
            payload = b"%PDF-1.4 topology ingest"
            atomic_write_file(destination=first_drop, content=(payload,))
            atomic_write_file(destination=second_drop, content=(payload,))

            assert first_drop.is_relative_to(reloaded_paths.IMPORT_DIR)
            assert second_drop.is_relative_to(reloaded_paths.IMPORT_DIR)
            assert not first_drop.is_relative_to(reloaded_paths.protected_media_root())
            assert (
                reloaded_paths.resolve_existing_protected_media_path(first_drop) is None
            )

            class _StubReportImportService:
                def import_and_anonymize(
                    self,
                    *,
                    file_path,
                    center_name,
                    retry,
                ):
                    assert Path(file_path) == first_drop
                    assert center_name == center.name
                    assert retry is False
                    file_hash = sha256_file(Path(file_path))
                    report = RawPdfFile(pdf_hash=file_hash, center=center)
                    save_local_file(
                        report.file,
                        Path(file_path),
                        name=f"{file_hash}.pdf",
                        save=False,
                    )
                    save_local_file(
                        report.processed_file,
                        Path(file_path),
                        name=f"{file_hash}.processed.pdf",
                        save=False,
                    )
                    report.save()
                    report.get_or_create_state().mark_anonymization_validated()
                    safe_unlink_file(Path(file_path), missing_ok=False)
                    return report

            monkeypatch.setattr(
                "endoreg_db.services.hub.ingest.ReportImportService",
                _StubReportImportService,
            )

            first_job = process_watcher_file(
                file_path=first_drop,
                file_type="report",
                center=center,
            )
            second_job = process_watcher_file(
                file_path=second_drop,
                file_type="report",
                center=center,
            )

            db_job = UploadJob.objects.get(id=first_job.id)
            assert first_job.id == second_job.id
            assert UploadJob.objects.count() == 1
            assert db_job.ingest_mode == UploadJob.IngestMode.WATCHER
            assert db_job.storage_class == UploadJob.StorageClass.INGEST
            assert db_job.storage_tier == UploadJob.StorageTier.UPLOAD_WATCHER
            assert (
                db_job.retention_policy
                == UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
            )
            assert db_job.status == UploadJob.Status.ANONYMIZED
            assert db_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
            assert db_job.source_file_delete_eligible_at is not None
            assert db_job.source_file_persisted is False
            assert db_job.source_center_id == center.id
            assert db_job.processing_provenance["entrypoint"] == "watcher"
            assert db_job.processing_provenance["file_type"] == "report"
            assert db_job.processing_provenance["watcher_processing_path"] == str(
                first_drop
            )
            assert (
                db_job.processing_provenance["source_center_key"] == center.center_key
            )
            assert db_job.processing_provenance["content_hash"] == db_job.content_hash
            assert db_job.file.name == ""
            assert first_drop.exists() is False
            assert second_drop.exists() is False
        finally:
            importlib.reload(paths_module)
