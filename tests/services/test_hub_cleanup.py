from __future__ import annotations

import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from collections.abc import Callable
from datetime import datetime
from typing import cast

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.utils import timezone

from endoreg_db.models import (
    Center,
    MediaOperationLease,
    ProcessingHistory,
    UploadJob,
    VideoFile,
)
from endoreg_db.services.hub import cleanup as cleanup_service
from endoreg_db.services.hub.cleanup import (
    UploadSourceCleanupBlocker,
    UploadSourceCleanupDecision,
    UploadSourceCleanupItem,
    apply_upload_job_source_cleanup,
    cleanup_upload_job_source,
    inspect_upload_job_source,
    reap_upload_job_sources,
    run_upload_job_source_reaper,
)
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityResult,
    MediaIntegrityStatus,
)


def _eligible_report_job(**overrides: object) -> UploadJob:
    override_values = dict(overrides)
    source_file = override_values.pop(
        "file",
        SimpleUploadedFile(
            name="cleanup.pdf",
            content=b"%PDF-1.4\n%%EOF\n",
            content_type="application/pdf",
        ),
    )
    if not isinstance(source_file, SimpleUploadedFile):
        raise AssertionError("test source must be a SimpleUploadedFile")
    source_bytes = source_file.read()
    source_file.seek(0)
    content_hash = hashlib.sha256(source_bytes).hexdigest()
    values: dict[str, object] = {
        "file": source_file,
        "content_type": "application/pdf",
        "content_hash": content_hash,
        "status": UploadJob.Status.ANONYMIZED,
        "retention_policy": UploadJob.RetentionPolicy.DELETE_AFTER_SUCCESS,
        "cleanup_status": UploadJob.CleanupStatus.ELIGIBLE,
        "source_file_persisted": True,
        "source_file_delete_eligible_at": timezone.now() - timedelta(seconds=1),
    }
    values.update(override_values)
    upload_job = UploadJob.objects.create(**values)
    if upload_job.content_hash:
        ProcessingHistory.objects.update_or_create(
            file_hash=upload_job.content_hash,
            defaults={"success": True},
        )
    return upload_job


def _eligible_video_job(**overrides: object) -> UploadJob:
    return _eligible_report_job(
        file=SimpleUploadedFile(
            name="cleanup.mp4",
            content=b"video-source",
            content_type="video/mp4",
        ),
        content_type="video/mp4",
        **overrides,
    )


def _target_video(upload_job: UploadJob) -> VideoFile:
    center = Center.objects.create(
        name=f"cleanup-center-{upload_job.pk}",
        display_name="Cleanup Center",
    )
    return VideoFile.objects.create(
        raw_file=SimpleUploadedFile("raw.mp4", b"raw"),
        processed_file=SimpleUploadedFile("processed.mp4", b"processed"),
        video_hash=upload_job.content_hash,
        processed_video_hash=f"processed-{upload_job.content_hash}",
        center=center,
    )


@pytest.fixture(autouse=True)
def _verified_target(  # pyright: ignore[reportUnusedFunction] -- discovered by pytest
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def verified_target(upload_job: UploadJob) -> MediaIntegrityResult:
        return MediaIntegrityResult(
            ok=True,
            status=MediaIntegrityStatus.OK,
            reason="verified",
            content_hash=upload_job.content_hash,
        )

    def verified_report_target(_upload_job: UploadJob) -> UploadSourceCleanupBlocker:
        return UploadSourceCleanupBlocker.NONE

    monkeypatch.setattr(
        cleanup_service,
        "check_upload_job_media_integrity",
        verified_target,
    )
    monkeypatch.setattr(
        cleanup_service,
        "_report_target_blocker",
        verified_report_target,
    )


@pytest.mark.django_db
def test_dry_run_reports_reclaimable_bytes_without_mutation() -> None:
    upload_job = _eligible_report_job()
    original_name = upload_job.file.name

    result = run_upload_job_source_reaper(apply=False, upload_job_id=upload_job.pk)

    upload_job.refresh_from_db()
    assert result.cleaned == 0
    assert result.reclaimable_bytes > 0
    assert result.items[0].decision == UploadSourceCleanupDecision.DELETE
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
    assert upload_job.file.name == original_name


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("factory", "ingest_mode", "expected_media_type"),
    [
        (_eligible_report_job, UploadJob.IngestMode.API, "report"),
        (_eligible_report_job, UploadJob.IngestMode.WATCHER, "report"),
        (_eligible_video_job, UploadJob.IngestMode.API, "video"),
        (_eligible_video_job, UploadJob.IngestMode.WATCHER, "video"),
    ],
)
def test_dry_run_classifies_report_video_watcher_and_api(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[..., UploadJob],
    ingest_mode: str,
    expected_media_type: str,
) -> None:
    def allow_target_integrity(
        _job: UploadJob,
        *,
        database_now: datetime,
    ) -> UploadSourceCleanupBlocker:
        assert database_now.tzinfo is not None
        return UploadSourceCleanupBlocker.NONE

    monkeypatch.setattr(
        cleanup_service,
        "_target_integrity_blocker",
        allow_target_integrity,
    )
    upload_job = factory(ingest_mode=ingest_mode)

    item = inspect_upload_job_source(upload_job)

    assert item.decision == UploadSourceCleanupDecision.DELETE
    assert item.media_type.value == expected_media_type
    assert item.ingest_mode == ingest_mode


@pytest.mark.django_db
def test_apply_deletes_existing_file_and_persists_receipt_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    upload_job = _eligible_report_job()
    caplog.set_level("INFO")

    result = apply_upload_job_source_cleanup(upload_job.pk)

    upload_job.refresh_from_db()
    assert result.applied is True
    assert result.freed_bytes > 0
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert upload_job.source_file_persisted is False
    assert upload_job.file.name == ""
    assert upload_job.cleanup_receipt_id is not None
    assert upload_job.cleanup_started_at is not None
    assert upload_job.cleanup_completed_at is not None
    assert upload_job.cleanup_fencing_token == 0
    events: list[dict[str, object]] = []
    for record in caplog.records:
        try:
            events.append(cast(dict[str, object], json.loads(record.getMessage())))
        except json.JSONDecodeError:
            continue
    names = {event.get("event") for event in events}
    assert "hub.upload_source_cleanup_authorized" in names
    assert "hub.upload_source_cleanup_completed" in names
    file_event = next(
        event for event in events if event.get("operation") == "storage_delete"
    )
    assert file_event["status"] == "ok"
    assert "source_path" not in file_event


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "blocker"),
    [
        (
            {"status": UploadJob.Status.PROCESSING},
            UploadSourceCleanupBlocker.STATUS_NOT_SUCCESSFUL,
        ),
        (
            {
                "status": UploadJob.Status.RETRYING,
                "retryable": True,
                "retry_count": 1,
                "next_retry_at": timezone.now() + timedelta(minutes=1),
                "error_code": UploadJob.ErrorCode.DISPATCH_UNAVAILABLE,
            },
            UploadSourceCleanupBlocker.RETRY_ALLOWED,
        ),
        (
            {"retention_policy": UploadJob.RetentionPolicy.PRESERVE_SOURCE},
            UploadSourceCleanupBlocker.RETENTION_POLICY_BLOCKS,
        ),
        (
            {"source_file_delete_eligible_at": timezone.now() + timedelta(days=1)},
            UploadSourceCleanupBlocker.NOT_DUE,
        ),
        (
            {
                "processing_lease_owner": "worker",
                "processing_lease_expires_at": timezone.now() + timedelta(minutes=5),
                "processing_heartbeat_at": timezone.now(),
            },
            UploadSourceCleanupBlocker.ACTIVE_PROCESSING_LEASE,
        ),
    ],
)
def test_authoritative_state_matrix_blocks_unsafe_jobs(
    overrides: dict[str, object],
    blocker: UploadSourceCleanupBlocker,
) -> None:
    upload_job = _eligible_report_job(**overrides)

    item = inspect_upload_job_source(upload_job)

    assert item.decision == UploadSourceCleanupDecision.BLOCKED
    assert item.blocker == blocker
    assert cleanup_upload_job_source(upload_job) is False


@pytest.mark.django_db
def test_missing_processing_history_blocks_cleanup() -> None:
    upload_job = _eligible_report_job()
    ProcessingHistory.objects.filter(file_hash=upload_job.content_hash).delete()

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.PROCESSING_HISTORY_MISSING


@pytest.mark.django_db
def test_failed_target_integrity_blocks_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()

    def failed_target(job: UploadJob) -> MediaIntegrityResult:
        return MediaIntegrityResult(
            ok=False,
            status=MediaIntegrityStatus.ARTIFACT_MISSING,
            reason="missing",
            content_hash=job.content_hash,
        )

    monkeypatch.setattr(
        cleanup_service,
        "check_upload_job_media_integrity",
        failed_target,
    )

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.TARGET_INTEGRITY_FAILED


@pytest.mark.django_db
def test_active_video_media_operation_lease_blocks_cleanup() -> None:
    upload_job = _eligible_video_job()
    video = _target_video(upload_job)
    MediaOperationLease.objects.create(
        video=video,
        lease_type=MediaOperationLease.LEASE_STREAM,
        expires_at=timezone.now() + timedelta(minutes=5),
        metadata={"file_type": "processed"},
    )

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.ACTIVE_MEDIA_OPERATION_LEASE


@pytest.mark.django_db
def test_hls_generation_mismatch_blocks_video_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_video_job()
    _target_video(upload_job)

    def mismatched_hls(**_kwargs: object) -> object:
        return type("Artifact", (), {"source_file_name": "other-generation.mp4"})()

    monkeypatch.setattr(cleanup_service, "get_ready_hls_artifact", mismatched_hls)

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.VIDEO_HLS_GENERATION_MISMATCH


@pytest.mark.django_db
def test_missing_source_without_receipt_is_not_marked_completed() -> None:
    upload_job = _eligible_report_job()
    upload_job.file.delete(save=False)

    item = apply_upload_job_source_cleanup(upload_job.pk)

    upload_job.refresh_from_db()
    assert item.blocker == UploadSourceCleanupBlocker.SOURCE_MISSING_UNEXPECTED
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
    assert upload_job.source_file_persisted is True
    assert upload_job.cleanup_receipt_id is None


@pytest.mark.django_db
def test_manipulated_storage_path_outside_boundary_is_blocked() -> None:
    upload_job = _eligible_report_job()
    UploadJob.objects.filter(pk=upload_job.pk).update(file="../../outside.pdf")
    upload_job.refresh_from_db()

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.SOURCE_PATH_UNSAFE


@pytest.mark.django_db
def test_database_failure_before_authorization_preserves_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()
    original_save = UploadJob.save

    def fail_authorization_save(
        self: UploadJob, *args: object, **kwargs: object
    ) -> None:
        if self.cleanup_status == UploadJob.CleanupStatus.DELETING.value:
            raise RuntimeError("injected database write failure before deletion")
        original_save(self, *args, **kwargs)

    monkeypatch.setattr(UploadJob, "save", fail_authorization_save)

    with pytest.raises(RuntimeError, match="before deletion"):
        apply_upload_job_source_cleanup(upload_job.pk)

    upload_job.refresh_from_db()
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE
    source_name = upload_job.file.name
    assert isinstance(source_name, str)
    assert upload_job.file.storage.exists(source_name)


@pytest.mark.django_db
def test_database_failure_after_mutation_is_reconciled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()
    original_save = UploadJob.save
    failed_once = False

    def fail_completion_save(self: UploadJob, *args: object, **kwargs: object) -> None:
        nonlocal failed_once
        if (
            self.cleanup_status == UploadJob.CleanupStatus.COMPLETED.value
            and not failed_once
        ):
            failed_once = True
            raise RuntimeError("injected database write failure after deletion")
        original_save(self, *args, **kwargs)

    monkeypatch.setattr(UploadJob, "save", fail_completion_save)
    first = apply_upload_job_source_cleanup(upload_job.pk)
    monkeypatch.setattr(UploadJob, "save", original_save)
    second = apply_upload_job_source_cleanup(upload_job.pk)

    assert first.blocker == UploadSourceCleanupBlocker.DELETE_FAILED
    assert second.applied is True
    assert second.freed_bytes == 0


@pytest.mark.django_db
def test_delete_failure_retains_reconciliable_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()

    def fail_delete(_field_file: object, *, missing_ok: bool) -> bool:
        assert missing_ok is False
        raise OSError("injected delete failure")

    monkeypatch.setattr(cleanup_service, "safe_delete_field_file", fail_delete)

    item = apply_upload_job_source_cleanup(upload_job.pk)

    upload_job.refresh_from_db()
    assert item.blocker == UploadSourceCleanupBlocker.DELETE_FAILED
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.DELETING
    assert upload_job.cleanup_receipt_id is not None
    assert upload_job.source_file_persisted is True
    assert upload_job.cleanup_failure_count == 1
    assert upload_job.cleanup_last_error_code == "delete_failed"


@pytest.mark.django_db
def test_same_name_same_size_source_replacement_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()

    def fail_delete(_field_file: object, *, missing_ok: bool) -> bool:
        assert missing_ok is False
        raise OSError("leave receipt")

    monkeypatch.setattr(cleanup_service, "safe_delete_field_file", fail_delete)
    apply_upload_job_source_cleanup(upload_job.pk)
    upload_job.refresh_from_db()
    storage_name = upload_job.file.name
    assert isinstance(storage_name, str)
    original_plaintext_size = len(b"%PDF-1.4\n%%EOF\n")
    upload_job.file.storage.delete(storage_name)
    replacement_name = upload_job.file.storage.save(
        storage_name,
        ContentFile(b"X" * original_plaintext_size),
    )
    assert replacement_name == storage_name

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.SOURCE_IDENTITY_CHANGED


@pytest.mark.django_db
def test_crash_after_file_delete_reconciles_on_next_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()
    real_delete = cleanup_service.safe_delete_field_file

    def delete_then_crash(field_file: object, *, missing_ok: bool) -> bool:
        deleted = real_delete(field_file, missing_ok=missing_ok)  # type: ignore[arg-type]
        assert deleted is True
        raise RuntimeError("injected process interruption")

    monkeypatch.setattr(cleanup_service, "safe_delete_field_file", delete_then_crash)
    first = apply_upload_job_source_cleanup(upload_job.pk)
    monkeypatch.setattr(cleanup_service, "safe_delete_field_file", real_delete)
    second = apply_upload_job_source_cleanup(upload_job.pk)

    upload_job.refresh_from_db()
    assert first.blocker == UploadSourceCleanupBlocker.DELETE_FAILED
    assert second.applied is True
    assert second.freed_bytes == 0
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert upload_job.source_file_persisted is False


@pytest.mark.django_db
def test_fencing_change_after_authorization_blocks_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_job = _eligible_report_job()

    def fail_delete(_field_file: object, *, missing_ok: bool) -> bool:
        assert missing_ok is False
        raise OSError("leave receipt")

    monkeypatch.setattr(cleanup_service, "safe_delete_field_file", fail_delete)
    apply_upload_job_source_cleanup(upload_job.pk)
    UploadJob.objects.filter(pk=upload_job.pk).update(processing_fencing_token=1)

    item = inspect_upload_job_source(upload_job)

    assert item.blocker == UploadSourceCleanupBlocker.FENCING_TOKEN_CHANGED


@pytest.mark.django_db
def test_reaper_respects_positive_limit() -> None:
    first = _eligible_report_job(
        file=SimpleUploadedFile("first.pdf", b"%PDF-1.4\nfirst\n%%EOF\n")
    )
    second = _eligible_report_job(
        file=SimpleUploadedFile("second.pdf", b"%PDF-1.4\nsecond\n%%EOF\n")
    )

    cleaned = reap_upload_job_sources(limit=1)

    first.refresh_from_db()
    second.refresh_from_db()
    assert cleaned == 1
    assert first.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert second.cleanup_status == UploadJob.CleanupStatus.ELIGIBLE


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_reapers_serialize_one_source_deletion() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("row-lock concurrency evidence requires PostgreSQL")
    upload_job = _eligible_report_job()
    barrier = threading.Barrier(2)

    def apply_in_thread() -> UploadSourceCleanupItem:
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return apply_upload_job_source_cleanup(upload_job.pk)
        finally:
            close_old_connections()

    def apply_indexed(_index: int) -> UploadSourceCleanupItem:
        return apply_in_thread()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply_indexed, range(2)))

    upload_job.refresh_from_db()
    assert sum(item.applied for item in results) == 1
    assert upload_job.cleanup_status == UploadJob.CleanupStatus.COMPLETED
    assert upload_job.source_file_persisted is False


@pytest.mark.django_db
def test_invalid_or_conflicting_service_selection_fails_loudly() -> None:
    with pytest.raises(ValueError, match="positive"):
        run_upload_job_source_reaper(apply=False, limit=0)
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_upload_job_source_reaper(
            apply=False,
            upload_job_id=_eligible_report_job().pk,
            limit=1,
        )
