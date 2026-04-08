from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from endoreg_db.models import Center, UploadJob
from endoreg_db.services.hub.ingest import process_watcher_file
from endoreg_db.utils import paths as paths_module


@pytest.mark.django_db
def test_watcher_ingest_uses_protected_runtime_topology_and_reuses_duplicate_content(
    monkeypatch,
):
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"

    with monkeypatch.context() as scoped:
        scoped.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", protected_root_rel)
        scoped.setenv("STORAGE_DIR", f"{protected_root_rel}/storage")
        scoped.setenv("IO_DIR", protected_root_rel)

        reloaded_paths = importlib.reload(paths_module)

        try:
            center = Center.objects.create(
                name=f"Topology Center {unique_suffix}",
                display_name="Topology Center",
            )
            first_drop = reloaded_paths.WATCHER_REPORT_DROP_DIR / "case-a.pdf"
            second_drop = reloaded_paths.WATCHER_REPORT_DROP_DIR / "case-b.pdf"
            payload = b"%PDF-1.4 topology ingest"
            first_drop.write_bytes(payload)
            second_drop.write_bytes(payload)

            class _StubReportImportService:
                def import_and_anonymize(
                    self,
                    *,
                    file_path,
                    center_name,
                    retry,
                    delete_source,
                ):
                    assert Path(file_path) == first_drop
                    assert center_name == center.name
                    assert retry is False
                    assert delete_source is True
                    Path(file_path).unlink(missing_ok=True)
                    return SimpleNamespace(sensitive_meta=None)

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
            managed_upload_path = Path(db_job.file.path).resolve()

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
            assert db_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
            assert db_job.source_file_delete_eligible_at is not None
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
            assert db_job.file.name.startswith("upload_jobs/watcher/")
            assert managed_upload_path.exists()
            assert managed_upload_path.is_relative_to(reloaded_paths.UPLOAD_WATCHER_DIR)
            assert managed_upload_path.is_relative_to(
                reloaded_paths.PROTECTED_DATA_ROOT
            )
            assert first_drop.exists() is False
            assert second_drop.exists() is False
        finally:
            importlib.reload(paths_module)
