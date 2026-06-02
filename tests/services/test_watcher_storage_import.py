from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from endoreg_db.models import Center, EndoscopyProcessor, UploadJob, VideoFile
from endoreg_db.services.hub import ingest
from endoreg_db.services.hub.watcher_handoff import (
    WatcherFileNotReadyError,
    is_in_progress_handoff_path,
)
from endoreg_db.utils.filesystem.file_operations import (
    atomic_write_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.storage import save_local_file


@pytest.fixture
def watcher_center() -> Center:
    return Center.objects.create(
        name="watcher-storage-center",
        display_name="Watcher Storage Center",
    )


def _write_test_file(path, content: bytes):
    return atomic_write_file(
        destination=path,
        content=(content,),
        required_bytes=len(content),
    )


def _create_completed_video_upload(
    *,
    tmp_path,
    watcher_center: Center,
    filename: str,
    content: bytes,
    include_raw: bool,
    include_processed: bool,
    storage_tier: str = UploadJob.StorageTier.UPLOAD_WATCHER,
    source_system: str = "watcher",
    processing_provenance: ingest.UploadProvenance | None = None,
) -> tuple[Path, UploadJob, VideoFile]:
    EndoscopyProcessor.objects.get_or_create(name="watcher-integrity-processor")
    watched_file = _write_test_file(tmp_path / filename, content)
    provenance: ingest.UploadProvenance = {"file_type": "video"}
    if processing_provenance is not None:
        provenance.update(processing_provenance)
    upload_job, created = ingest.create_or_reuse_watcher_upload_job(
        file_path=watched_file,
        content_type="video/mp4",
        source_center=watcher_center,
        source_system=source_system,
        storage_tier=storage_tier,
        processing_provenance=provenance,
    )
    assert created is True

    file_hash = sha256_file(watched_file)
    video = VideoFile.objects.create(
        center=watcher_center,
        original_file_name=filename,
        video_hash=file_hash,
        suffix=".mp4",
    )
    update_fields: list[str] = []
    if include_raw:
        raw_source = _write_test_file(tmp_path / f"raw-{filename}", content)
        save_local_file(
            video.raw_file,
            raw_source,
            name=f"{file_hash}.mp4",
            save=False,
        )
        update_fields.append("raw_file")
    if include_processed:
        processed_source = _write_test_file(
            tmp_path / f"processed-{filename}",
            b"processed:" + content,
        )
        processed_hash = sha256_file(processed_source)
        save_local_file(
            video.processed_file,
            processed_source,
            name=f"{processed_hash}.mp4",
            save=False,
        )
        video.processed_video_hash = processed_hash
        update_fields.extend(["processed_file", "processed_video_hash"])
    if update_fields:
        video.save(update_fields=update_fields)
    video.get_or_create_state().mark_anonymization_validated()
    upload_job.mark_completed()
    return watched_file, upload_job, video


def _fake_video_import(video: VideoFile):
    def _import_and_anonymize(*, file_path, center_name, processor_name, retry):
        assert Path(file_path).exists()
        safe_unlink_file(Path(file_path), missing_ok=False)
        return video

    return _import_and_anonymize


@pytest.mark.parametrize(
    "filename",
    [
        "slow.tmp",
        "slow.part",
        "slow.partial",
        "slow.crdownload",
        "slow.download",
        "slow.mp4.tmp.123",
        "slow.mp4.part.123",
    ],
)
@pytest.mark.unit
def test_wait_for_watcher_file_ready_rejects_atomic_handoff_marker(
    tmp_path,
    filename: str,
):
    watched_file = tmp_path / filename
    watched_file.write_bytes(b"partial-video")

    assert is_in_progress_handoff_path(watched_file) is True
    with pytest.raises(WatcherFileNotReadyError, match="in-progress handoff"):
        ingest._wait_for_watcher_file_ready(
            watched_file,
            stable_after_seconds=0,
            poll_interval_seconds=0.01,
        )


@pytest.mark.django_db
def test_process_watcher_file_waits_for_direct_slow_writer(
    tmp_path,
    watcher_center: Center,
    monkeypatch,
):
    initial_content = b"partial-"
    final_suffix = b"complete"
    expected_content = initial_content + final_suffix
    watched_file = tmp_path / "direct-slow-writer.mp4"
    watched_file.write_bytes(initial_content)
    writer_errors: list[BaseException] = []

    def finish_write() -> None:
        try:
            time.sleep(0.03)
            with watched_file.open("ab") as handle:
                handle.write(final_suffix)
            os.utime(watched_file, None)
        except BaseException as exc:  # pragma: no cover - surfaced below
            writer_errors.append(exc)

    writer_thread = threading.Thread(target=finish_write, daemon=True)
    writer_thread.start()

    EndoscopyProcessor.objects.get_or_create(name="slow-writer-processor")
    monkeypatch.setenv("WATCHER_STABLE_AFTER_SECONDS", "0.12")
    monkeypatch.setenv("WATCHER_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setattr(
        ingest,
        "start_upload_job_processing",
        lambda **kwargs: "test-handoff",
        raising=True,
    )

    try:
        upload_job = ingest.process_watcher_file(
            file_path=watched_file,
            file_type="video",
            center=watcher_center,
            processor_name="slow-writer-processor",
        )
    finally:
        writer_thread.join(timeout=1)

    assert writer_errors == []
    assert not watched_file.exists()
    assert upload_job.content_hash == sha256_file(upload_job.file)
    with upload_job.file.open("rb") as handle:
        assert handle.read() == expected_content


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


@pytest.mark.django_db
def test_completed_watcher_video_with_intact_media_reuses_duplicate_drop(
    tmp_path,
    watcher_center: Center,
):
    watched_file, upload_job, _video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="complete-video.mp4",
        content=b"complete-video",
        include_raw=True,
        include_processed=True,
    )

    with patch(
        "endoreg_db.services.hub.ingest.VideoImportService.import_and_anonymize",
        side_effect=AssertionError("complete media must be reused"),
    ):
        reused_job = ingest.process_watcher_file(
            file_path=watched_file,
            file_type="video",
            center=watcher_center,
        )

    upload_job.refresh_from_db()
    assert reused_job.id == upload_job.id
    assert upload_job.status == UploadJob.Status.ANONYMIZED
    assert not watched_file.exists()


@pytest.mark.django_db
def test_completed_watcher_video_missing_raw_marks_old_job_lost_and_reingests(
    tmp_path,
    watcher_center: Center,
):
    watched_file, upload_job, video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="missing-raw.mp4",
        content=b"missing-raw-video",
        include_raw=False,
        include_processed=True,
    )

    with patch(
        "endoreg_db.services.hub.ingest.VideoImportService.import_and_anonymize",
        side_effect=_fake_video_import(video),
    ):
        new_job = ingest.process_watcher_file(
            file_path=watched_file,
            file_type="video",
            center=watcher_center,
        )

    upload_job.refresh_from_db()
    new_job.refresh_from_db()
    assert new_job.id != upload_job.id
    assert upload_job.status == UploadJob.Status.LOST
    assert new_job.status == UploadJob.Status.ANONYMIZED
    assert new_job.processing_provenance["previous_upload_job_id"] == str(upload_job.id)
    assert new_job.processing_provenance["media_integrity_status"] == (
        "artifact_missing"
    )
    assert (
        "raw_file" in new_job.processing_provenance["media_integrity_missing_artifacts"]
    )


@pytest.mark.django_db
def test_completed_watcher_video_missing_processed_marks_old_job_lost_and_reingests(
    tmp_path,
    watcher_center: Center,
):
    watched_file, upload_job, video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="missing-processed.mp4",
        content=b"missing-processed-video",
        include_raw=True,
        include_processed=False,
    )

    with patch(
        "endoreg_db.services.hub.ingest.VideoImportService.import_and_anonymize",
        side_effect=_fake_video_import(video),
    ):
        new_job = ingest.process_watcher_file(
            file_path=watched_file,
            file_type="video",
            center=watcher_center,
        )

    upload_job.refresh_from_db()
    new_job.refresh_from_db()
    assert new_job.id != upload_job.id
    assert upload_job.status == UploadJob.Status.LOST
    assert new_job.processing_provenance["media_integrity_status"] == (
        "artifact_missing"
    )
    assert (
        "processed_file"
        in new_job.processing_provenance["media_integrity_missing_artifacts"]
    )


@pytest.mark.django_db
def test_completed_watcher_video_unreadable_artifact_marks_old_job_lost(
    tmp_path,
    watcher_center: Center,
):
    watched_file, upload_job, video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="unreadable-artifact.mp4",
        content=b"unreadable-artifact-video",
        include_raw=True,
        include_processed=True,
    )

    with (
        patch(
            "endoreg_db.services.hub.media_integrity.field_file_is_readable",
            return_value=False,
        ),
        patch(
            "endoreg_db.services.hub.ingest.VideoImportService.import_and_anonymize",
            side_effect=_fake_video_import(video),
        ),
    ):
        new_job = ingest.process_watcher_file(
            file_path=watched_file,
            file_type="video",
            center=watcher_center,
        )

    upload_job.refresh_from_db()
    new_job.refresh_from_db()
    assert new_job.id != upload_job.id
    assert upload_job.status == UploadJob.Status.LOST
    assert new_job.processing_provenance["media_integrity_status"] == (
        "artifact_unreadable"
    )


@pytest.mark.django_db
def test_completed_preanonymized_video_does_not_require_raw_file_for_reuse(
    tmp_path,
    watcher_center: Center,
):
    watched_file, upload_job, _video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="preanonymized-complete.mp4",
        content=b"preanonymized-complete-video",
        include_raw=False,
        include_processed=True,
        storage_tier=UploadJob.StorageTier.UPLOAD_PREANONYMIZED,
        source_system="watcher_preanonymized",
        processing_provenance={"ingest_variant": "preanonymized"},
    )
    sidecar_path = _write_test_file(watched_file.with_suffix(".json"), b"{}")

    with patch(
        "endoreg_db.services.hub.ingest._finalize_preanonymized_video",
        side_effect=AssertionError("preanonymized complete media must be reused"),
    ):
        reused_job = ingest.process_preanonymized_watcher_file(
            file_path=watched_file,
            center=watcher_center,
        )

    upload_job.refresh_from_db()
    assert reused_job.id == upload_job.id
    assert upload_job.status == UploadJob.Status.ANONYMIZED
    assert not watched_file.exists()
    assert not sidecar_path.exists()


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


@pytest.mark.django_db
def test_create_or_reuse_watcher_upload_job_defers_when_file_changes_after_hash(
    tmp_path,
    watcher_center: Center,
    monkeypatch,
):
    watched_file = tmp_path / "changing-report.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def mutate_during_hash(path):
        with Path(path).open("ab") as handle:
            handle.write(b"late-bytes")
        os.utime(path, None)
        return "hash-before-change"

    monkeypatch.setattr(ingest, "sha256_file", mutate_during_hash)

    with pytest.raises(WatcherFileNotReadyError, match="changed after settle"):
        ingest.create_or_reuse_watcher_upload_job(
            file_path=watched_file,
            content_type="application/pdf",
            source_center=watcher_center,
        )

    assert watched_file.exists()
    assert UploadJob.objects.count() == 0
