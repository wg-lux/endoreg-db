# pyright: reportPrivateUsage=false
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Protocol
from unittest.mock import patch

import pytest
from lx_dtypes.models.contracts.hub_ingest import (
    UploadProvenancePayload,
    parse_upload_provenance_payload,
)

from endoreg_db.models import Center, EndoscopyProcessor, UploadJob, VideoFile
from endoreg_db.services.hub import ingest
from endoreg_db.services.hub import watcher_handoff
from endoreg_db.services.hub.watcher_handoff import (
    WatcherFileNotReadyError,
    is_in_progress_handoff_path,
)
from endoreg_db.utils.file_operations import (
    atomic_write_file,
    safe_unlink_file,
    sha256_file,
)
from endoreg_db.utils.storage import save_local_file


class _VideoImportCallable(Protocol):
    def __call__(
        self,
        *,
        file_path: Path,
        center_name: str,
        processor_name: str,
        retry: bool,
        attempt_id: str | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> VideoFile: ...


@pytest.fixture
def watcher_center() -> Center:
    return Center.objects.create(
        name="watcher-storage-center",
        display_name="Watcher Storage Center",
    )


def _write_test_file(path: Path, content: bytes) -> Path:
    return atomic_write_file(
        destination=path,
        content=(content,),
        required_bytes=len(content),
    )


def _validated_upload_provenance(upload_job: UploadJob) -> UploadProvenancePayload:
    return parse_upload_provenance_payload(upload_job.processing_provenance)


def _create_completed_video_upload(
    *,
    tmp_path: Path,
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


def _fake_video_import(video: VideoFile) -> _VideoImportCallable:
    def _import_and_anonymize(
        *,
        file_path: Path,
        center_name: str,
        processor_name: str,
        retry: bool,
        attempt_id: str | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> VideoFile:
        assert attempt_id
        assert execution_guard is not None
        execution_guard()
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
    tmp_path: Path,
    filename: str,
) -> None:
    watched_file = tmp_path / filename
    watched_file.write_bytes(b"partial-video")

    assert is_in_progress_handoff_path(watched_file) is True
    with pytest.raises(WatcherFileNotReadyError, match="in-progress handoff") as error:
        ingest._wait_for_watcher_file_ready(
            watched_file,
            stable_after_seconds=0,
            poll_interval_seconds=0.01,
        )

    assert str(watched_file) not in str(error.value)
    assert watched_file.name not in str(error.value)
    assert "path_sha256=" in str(error.value)


@pytest.mark.unit
def test_wait_for_watcher_file_ready_rejects_symbolic_link(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    watched_link = tmp_path / "linked.mp4"
    watched_link.symlink_to(source)

    with pytest.raises(ValueError, match="symbolic link") as error:
        ingest._wait_for_watcher_file_ready(
            watched_link,
            stable_after_seconds=0,
            poll_interval_seconds=0.01,
        )

    assert str(watched_link) not in str(error.value)
    assert watched_link.name not in str(error.value)
    assert "path_sha256=" in str(error.value)


@pytest.mark.unit
def test_watcher_stat_and_timeout_errors_redact_source_path(tmp_path: Path) -> None:
    missing_file = tmp_path / "patient-max-mustermann-missing.mp4"
    with pytest.raises(FileNotFoundError, match="Watcher file not found") as missing:
        watcher_handoff.watcher_file_stat(missing_file)

    directory = tmp_path / "patient-max-mustermann-directory.mp4"
    directory.mkdir()
    with pytest.raises(ValueError, match="not a regular file") as not_regular:
        watcher_handoff.watcher_file_stat(directory)

    empty_file = tmp_path / "patient-max-mustermann-empty.mp4"
    empty_file.touch()
    with pytest.raises(
        WatcherFileNotReadyError,
        match="did not become stable",
    ) as timeout:
        watcher_handoff.wait_for_watcher_file_ready(
            empty_file,
            stable_after_seconds=0,
            poll_interval_seconds=0.001,
            timeout_seconds=0.001,
        )

    for error, path in (
        (missing.value, missing_file),
        (not_regular.value, directory),
        (timeout.value, empty_file),
    ):
        message = str(error)
        assert str(path) not in message
        assert path.name not in message
        assert "path_sha256=" in message


def _standard_watcher_entrypoint(path: Path) -> object:
    return ingest.process_watcher_file(file_path=path, file_type="video")


def _preanonymized_watcher_entrypoint(path: Path) -> object:
    return ingest.process_preanonymized_watcher_file(file_path=path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "entrypoint",
    [
        _standard_watcher_entrypoint,
        _preanonymized_watcher_entrypoint,
    ],
)
def test_watcher_entrypoint_missing_file_errors_redact_source_path(
    tmp_path: Path,
    entrypoint: Callable[[Path], object],
) -> None:
    missing_file = tmp_path / "patient-max-mustermann-missing.mp4"

    with pytest.raises(FileNotFoundError, match="Watcher file not found") as error:
        entrypoint(missing_file)

    assert str(missing_file) not in str(error.value)
    assert missing_file.name not in str(error.value)
    assert "path_sha256=" in str(error.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stable_after", "poll_interval", "timeout", "expected_label"),
    [
        (-0.1, 0.01, 2.0, "stable_after_seconds"),
        (float("nan"), 0.01, 2.0, "stable_after_seconds"),
        (0.0, 0.0, 2.0, "poll_interval_seconds"),
        (0.0, float("inf"), 2.0, "poll_interval_seconds"),
        (0.0, 0.01, 0.0, "timeout_seconds"),
        (0.0, 0.01, float("nan"), "timeout_seconds"),
    ],
)
def test_wait_for_watcher_file_ready_rejects_invalid_durations(
    tmp_path: Path,
    stable_after: float,
    poll_interval: float,
    timeout: float,
    expected_label: str,
) -> None:
    watched_file = tmp_path / "ready.mp4"
    watched_file.write_bytes(b"video")

    with pytest.raises(ValueError, match=expected_label):
        watcher_handoff.wait_for_watcher_file_ready(
            watched_file,
            stable_after_seconds=stable_after,
            poll_interval_seconds=poll_interval,
            timeout_seconds=timeout,
        )


@pytest.mark.unit
def test_wait_for_watcher_file_ready_honors_full_stability_window(
    tmp_path: Path,
) -> None:
    watched_file = tmp_path / "ready.mp4"
    watched_file.write_bytes(b"video")
    clock = {"now": 0.0}

    def monotonic() -> float:
        return clock["now"]

    def advance_clock(seconds: float) -> None:
        clock["now"] += seconds

    with (
        patch.object(watcher_handoff.time, "monotonic", side_effect=monotonic),
        patch.object(watcher_handoff.time, "sleep", side_effect=advance_clock),
    ):
        result = watcher_handoff.wait_for_watcher_file_ready(
            watched_file,
            stable_after_seconds=2.5,
            poll_interval_seconds=0.5,
            timeout_seconds=0.1,
        )

    assert result.st_size == len(b"video")
    assert clock["now"] >= 2.5


@pytest.mark.django_db
def test_process_watcher_file_waits_for_direct_slow_writer(
    tmp_path: Path,
    watcher_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
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

    def fake_start_upload_job_processing(
        *, upload_job: UploadJob, task_dispatcher: object
    ) -> str:
        _ = task_dispatcher
        return "test-handoff"

    monkeypatch.setattr(
        ingest,
        "start_upload_job_processing",
        fake_start_upload_job_processing,
        raising=True,
    )

    try:
        with caplog.at_level("INFO", logger=watcher_handoff.__name__):
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
    settle_events = [
        getattr(record, "structured_event", {})
        for record in caplog.records
        if getattr(record, "structured_event", {}).get("event")
        == "watcher.file_changed_during_settle"
    ]
    assert settle_events
    assert "path_sha256" in settle_events[-1]["file"]
    assert str(watched_file) not in caplog.text
    assert watched_file.name not in caplog.text


@pytest.mark.django_db
def test_create_or_reuse_watcher_upload_job_records_storage_contract(
    tmp_path: Path,
    watcher_center: Center,
) -> None:
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
    provenance = _validated_upload_provenance(upload_job)
    assert provenance.entrypoint == "watcher"
    assert provenance.watched_path == str(watched_file)
    assert provenance.file_type == "report"
    assert provenance.storage_tier == UploadJob.StorageTier.UPLOAD_WATCHER
    assert provenance.retention_policy == (
        UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS
    )


@pytest.mark.django_db
def test_process_watcher_file_reuse_deletes_duplicate_drop_without_reprocessing(
    tmp_path: Path,
    watcher_center: Center,
) -> None:
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
        "endoreg_db.services.report_import.ReportImportService.import_and_anonymize",
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
    tmp_path: Path,
    watcher_center: Center,
) -> None:
    watched_file, upload_job, _video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="complete-video.mp4",
        content=b"complete-video",
        include_raw=True,
        include_processed=True,
    )

    with patch(
        "endoreg_db.services.video_import.VideoImportService.import_and_anonymize",
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
    tmp_path: Path,
    watcher_center: Center,
) -> None:
    watched_file, upload_job, video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="missing-raw.mp4",
        content=b"missing-raw-video",
        include_raw=False,
        include_processed=True,
    )

    with patch(
        "endoreg_db.services.video_import.VideoImportService.import_and_anonymize",
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
    provenance = _validated_upload_provenance(new_job)
    assert provenance.previous_upload_job_id == str(upload_job.id)
    assert provenance.media_integrity_status == "artifact_missing"
    assert "raw_file" in provenance.media_integrity_missing_artifacts


@pytest.mark.django_db
def test_completed_watcher_video_missing_processed_marks_old_job_lost_and_reingests(
    tmp_path: Path,
    watcher_center: Center,
) -> None:
    watched_file, upload_job, video = _create_completed_video_upload(
        tmp_path=tmp_path,
        watcher_center=watcher_center,
        filename="missing-processed.mp4",
        content=b"missing-processed-video",
        include_raw=True,
        include_processed=False,
    )

    with patch(
        "endoreg_db.services.video_import.VideoImportService.import_and_anonymize",
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
    provenance = _validated_upload_provenance(new_job)
    assert provenance.media_integrity_status == "artifact_missing"
    assert "processed_file" in provenance.media_integrity_missing_artifacts


@pytest.mark.django_db
def test_completed_watcher_video_unreadable_artifact_marks_old_job_lost(
    tmp_path: Path,
    watcher_center: Center,
) -> None:
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
            "endoreg_db.services.video_import.VideoImportService.import_and_anonymize",
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
    provenance = _validated_upload_provenance(new_job)
    assert provenance.media_integrity_status == "artifact_unreadable"


@pytest.mark.django_db
def test_completed_preanonymized_video_does_not_require_raw_file_for_reuse(
    tmp_path: Path,
    watcher_center: Center,
) -> None:
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
def test_persist_preanonymized_file_moves_source_when_delete_source_requested(
    tmp_path: Path,
) -> None:
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
def test_persist_preanonymized_file_copies_source_when_delete_source_is_false(
    tmp_path: Path,
) -> None:
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
    tmp_path: Path,
) -> None:
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
def test_load_preanonymized_sidecar_rejects_non_object_payload(
    tmp_path: Path,
) -> None:
    watched_file = tmp_path / "patient-max-mustermann.pdf"
    sidecar = watched_file.with_suffix(".json")
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    sidecar.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a mapping") as error:
        ingest._load_preanonymized_sidecar(watched_file)

    assert str(sidecar) not in str(error.value)
    assert sidecar.name not in str(error.value)
    assert "path_sha256=" in str(error.value)


@pytest.mark.unit
def test_load_preanonymized_sidecar_accepts_yaml_mapping(tmp_path: Path) -> None:
    watched_file = tmp_path / "preanonymized.pdf"
    sidecar = watched_file.with_suffix(".yaml")
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    sidecar.write_text(
        "external_id: ext-42\nexternal_id_origin: hospital\n",
        encoding="utf-8",
    )

    payload, loaded_path = ingest._load_preanonymized_sidecar(watched_file)

    assert payload is not None
    assert payload.external_id == "ext-42"
    assert payload.external_id_origin == "hospital"
    assert loaded_path == sidecar


@pytest.mark.unit
def test_load_preanonymized_sidecar_rejects_multiple_formats(
    tmp_path: Path,
) -> None:
    watched_file = tmp_path / "patient-max-mustermann.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    watched_file.with_suffix(".json").write_text("{}", encoding="utf-8")
    watched_file.with_suffix(".yaml").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="multiple sidecars") as error:
        ingest._load_preanonymized_sidecar(watched_file)

    assert str(tmp_path) not in str(error.value)
    assert watched_file.stem not in str(error.value)
    assert str(error.value).count("path_sha256=") == 2


@pytest.mark.unit
def test_load_preanonymized_sidecar_errors_redact_missing_and_invalid_paths(
    tmp_path: Path,
) -> None:
    watched_file = tmp_path / "patient-max-mustermann.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")

    with pytest.raises(ValueError, match="sidecar is required") as missing:
        ingest._load_preanonymized_sidecar(watched_file, strict=True)

    sidecar = watched_file.with_suffix(".json")
    sidecar.write_text('{"unexpected_field": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid preanonymized sidecar") as invalid:
        ingest._load_preanonymized_sidecar(watched_file, strict=True)

    for error in (missing.value, invalid.value):
        message = str(error)
        assert str(tmp_path) not in message
        assert watched_file.stem not in message
        assert "path_sha256=" in message


@pytest.mark.django_db
def test_preanonymized_quarantine_logs_use_opaque_path_references(
    tmp_path: Path,
    watcher_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    watched_file = tmp_path / "patient-max-mustermann.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    sidecar = watched_file.with_suffix(".json")
    sidecar.write_text("{}", encoding="utf-8")
    quarantine_dir = tmp_path / "quarantine"
    quarantine_dir.mkdir()
    upload_job = UploadJob.objects.create(
        source_center=watcher_center,
        processing_provenance={},
    )
    monkeypatch.setattr(ingest, "_quarantine_dir", lambda: quarantine_dir)

    with caplog.at_level("WARNING", logger=ingest.__name__):
        ingest._quarantine_failed_preanonymized_media(
            upload_job=upload_job,
            watched_path=watched_file,
        )
        ingest._quarantine_failed_preanonymized_sidecar(
            upload_job=upload_job,
            sidecar_path=sidecar,
        )

    events = [
        getattr(record, "structured_event", {})
        for record in caplog.records
        if getattr(record, "structured_event", {}).get("event")
        in {
            "watcher.quarantine_media_moved",
            "watcher.quarantine_sidecar_moved",
        }
    ]
    assert len(events) == 2
    for event in events:
        assert "path_sha256" in event["source"]
        assert "path_sha256" in event["destination"]
    assert str(tmp_path) not in caplog.text
    assert watched_file.name not in caplog.text
    assert sidecar.name not in caplog.text


@pytest.mark.django_db
def test_preanonymized_quarantine_failure_log_redacts_path_bearing_error(
    tmp_path: Path,
    watcher_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    watched_file = tmp_path / "patient-max-mustermann.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
    upload_job = UploadJob.objects.create(
        source_center=watcher_center,
        processing_provenance={},
    )
    monkeypatch.setattr(ingest, "_quarantine_dir", lambda: tmp_path / "quarantine")

    def fail_move(*, source: Path, destination: Path) -> None:
        raise OSError(f"move failed from {source} to {destination}")

    monkeypatch.setattr(ingest, "atomic_move_file", fail_move)

    with caplog.at_level("ERROR", logger=ingest.__name__):
        ingest._quarantine_failed_preanonymized_media(
            upload_job=upload_job,
            watched_path=watched_file,
        )

    events = [
        getattr(record, "structured_event", {})
        for record in caplog.records
        if getattr(record, "structured_event", {}).get("event")
        == "watcher.quarantine_media_move_failed"
    ]
    assert len(events) == 1
    assert "path_sha256" in events[0]["source"]
    assert str(tmp_path) not in caplog.text
    assert watched_file.name not in caplog.text


@pytest.mark.django_db
def test_watcher_handoff_failure_logs_redact_source_and_error_paths(
    tmp_path: Path,
    watcher_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    watched_file = tmp_path / "patient-max-mustermann.mp4"
    watched_file.write_bytes(b"video")
    upload_job = UploadJob.objects.create(
        source_center=watcher_center,
        processing_provenance={},
    )
    assert upload_job.schedule_retry(
        "test retry",
        error_code=UploadJob.ErrorCode.DISPATCH_UNAVAILABLE,
        delay_seconds=1,
    )
    technical_error = ConnectionRefusedError(
        f"broker rejected watcher source {watched_file}"
    )
    monkeypatch.setattr(
        ingest.settings,
        "WATCHER_CELERY_INLINE_FALLBACK_ENABLED",
        False,
    )

    with caplog.at_level("WARNING", logger=ingest.__name__):
        result = ingest._handle_watcher_handoff_failure(
            upload_job=upload_job,
            watched_path=watched_file,
            normalized_type="video",
            source_center=watcher_center,
            effective_processor_name=None,
            exc=technical_error,
        )

    assert result is not None
    event_names = {
        getattr(record, "structured_event", {}).get("event")
        for record in caplog.records
    }
    assert "watcher.celery_handoff_failed" in event_names
    assert "watcher.processing_handoff_failed" in event_names
    assert str(tmp_path) not in caplog.text
    assert watched_file.name not in caplog.text


@pytest.mark.unit
def test_opportunistic_reap_watcher_sources_fails_open_for_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_reap_upload_job_sources(*, limit: int) -> int:
        raise RuntimeError("cleanup unavailable")

    monkeypatch.setattr(
        ingest,
        "reap_upload_job_sources",
        fail_reap_upload_job_sources,
    )

    assert ingest._opportunistic_reap_watcher_sources(limit=7) == 0


@pytest.mark.django_db
def test_create_or_reuse_watcher_upload_job_uses_file_stat_in_idempotency_key(
    tmp_path: Path,
    watcher_center: Center,
) -> None:
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
    tmp_path: Path,
    watcher_center: Center,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    watched_file = tmp_path / "changing-report.pdf"
    watched_file.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def mutate_during_hash(path: Path) -> str:
        with Path(path).open("ab") as handle:
            handle.write(b"late-bytes")
        os.utime(path, None)
        return "hash-before-change"

    monkeypatch.setattr(ingest, "sha256_file", mutate_during_hash)

    with (
        caplog.at_level("WARNING", logger=watcher_handoff.__name__),
        pytest.raises(WatcherFileNotReadyError, match="changed after settle") as error,
    ):
        ingest.create_or_reuse_watcher_upload_job(
            file_path=watched_file,
            content_type="application/pdf",
            source_center=watcher_center,
        )

    assert watched_file.exists()
    assert UploadJob.objects.count() == 0
    events = [
        getattr(record, "structured_event", {})
        for record in caplog.records
        if getattr(record, "structured_event", {}).get("event")
        == "watcher.file_changed_after_settle"
    ]
    assert len(events) == 1
    assert "path_sha256" in events[0]["file"]
    assert str(watched_file) not in caplog.text
    assert watched_file.name not in caplog.text
    assert str(watched_file) not in str(error.value)
    assert watched_file.name not in str(error.value)
