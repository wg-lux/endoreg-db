from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from endoreg_db.models import Center, UploadJob
from endoreg_db.services.hub import ingest


@pytest.fixture
def watcher_center() -> Center:
    return Center.objects.create(
        name="watcher-storage-center",
        display_name="Watcher Storage Center",
    )


@pytest.mark.django_db
def test_create_or_reuse_watcher_upload_job_records_storage_contract(
    tmp_path,
    watcher_center: Center,
):
    watched_file = tmp_path / "incoming-report.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")

    upload_job, created = ingest.create_or_reuse_watcher_upload_job(
        file_path=watched_file,
        content_type="application/pdf",
        source_center=watcher_center,
        processing_provenance={"file_type": "report"},
    )

    upload_job.refresh_from_db()
    assert created is True
    assert upload_job.ingest_mode == UploadJob.IngestMode.WATCHER
    assert upload_job.storage_tier == UploadJob.StorageTier.UPLOAD_WATCHER
    assert upload_job.retention_policy == UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.PENDING
    assert upload_job.source_file_persisted is True
    assert upload_job.original_filename == watched_file.name
    assert upload_job.processing_provenance["entrypoint"] == "watcher"
    assert upload_job.processing_provenance["watched_path"] == str(watched_file)
    assert upload_job.processing_provenance["file_type"] == "report"
    assert (
        upload_job.processing_provenance["storage_tier"]
        == UploadJob.StorageTier.UPLOAD_WATCHER
    )
    assert upload_job.processing_provenance["retention_policy"] == (
        UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
    )


@pytest.mark.django_db
def test_process_watcher_file_reuse_deletes_duplicate_drop_without_reprocessing(
    tmp_path,
    watcher_center: Center,
):
    watched_file = tmp_path / "duplicate-report.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")

    upload_job, created = ingest.create_or_reuse_watcher_upload_job(
        file_path=watched_file,
        content_type="application/pdf",
        source_center=watcher_center,
        processing_provenance={"file_type": "report"},
    )
    assert created is True
    original_id = upload_job.id

    with patch(
        "endoreg_db.services.hub.ingest.ReportImportService.import_and_anonymize",
        side_effect=AssertionError("duplicate watcher file must not be reprocessed"),
    ):
        reused_job = ingest.process_watcher_file(
            file_path=watched_file,
            file_type="report",
            center=watcher_center,
        )

    reused_job.refresh_from_db()
    assert reused_job.id == original_id
    assert reused_job.status == UploadJob.Status.PENDING
    assert not watched_file.exists()


@pytest.mark.unit
def test_persist_preanonymized_file_moves_source_when_delete_source_requested(tmp_path):
    source = tmp_path / "drop" / "video.mp4"
    target = tmp_path / "managed" / "video.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"processed-video")

    ingest._persist_preanonymized_file(
        source_path=source,
        target_path=target,
        delete_source=True,
    )

    assert not source.exists()
    assert target.read_bytes() == b"processed-video"


@pytest.mark.unit
def test_persist_preanonymized_file_copies_source_when_delete_source_is_false(tmp_path):
    source = tmp_path / "drop" / "report.pdf"
    target = tmp_path / "managed" / "report.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")

    ingest._persist_preanonymized_file(
        source_path=source,
        target_path=target,
        delete_source=False,
    )

    assert source.read_bytes() == b"%PDF-1.4\n%%EOF\n"
    assert target.read_bytes() == b"%PDF-1.4\n%%EOF\n"


@pytest.mark.unit
def test_persist_preanonymized_file_unlinks_duplicate_source_when_target_exists(
    tmp_path,
):
    source = tmp_path / "drop" / "video.mp4"
    target = tmp_path / "managed" / "video.mp4"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_bytes(b"duplicate-source")
    target.write_bytes(b"canonical-target")

    ingest._persist_preanonymized_file(
        source_path=source,
        target_path=target,
        delete_source=True,
    )

    assert not source.exists()
    assert target.read_bytes() == b"canonical-target"


@pytest.mark.unit
def test_load_preanonymized_sidecar_rejects_non_object_payload(tmp_path):
    watched_file = tmp_path / "preanonymized.pdf"
    sidecar = watched_file.with_suffix(".json")
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    sidecar.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON object"):
        ingest._load_preanonymized_sidecar(watched_file)


@pytest.mark.unit
def test_opportunistic_reap_watcher_sources_fails_open_for_ingest(monkeypatch):
    monkeypatch.setattr(
        ingest,
        "reap_upload_job_sources",
        lambda *, limit: (_ for _ in ()).throw(RuntimeError("cleanup unavailable")),
    )

    assert ingest._opportunistic_reap_watcher_sources(limit=7) == 0


@pytest.mark.django_db
def test_create_or_reuse_watcher_upload_job_uses_file_stat_in_idempotency_key(
    tmp_path,
    watcher_center: Center,
):
    watched_file = tmp_path / "stable-report.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    os.utime(watched_file, ns=(123_000_000_000, 456_000_000_000))

    with patch("endoreg_db.services.hub.ingest.sha256_file", return_value="hash-123"):
        upload_job, _created = ingest.create_or_reuse_watcher_upload_job(
            file_path=watched_file,
            content_type="application/pdf",
            source_center=watcher_center,
        )

    assert upload_job.idempotency_key == (
        f"watcher:hash-123:456000000000:{watched_file.stat().st_size}"
    )
