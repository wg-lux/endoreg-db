from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.upload_job import UploadJob
from endoreg_db.services.hub import media_integrity
from endoreg_db.services.hub.import_monitoring import STALE_UPLOAD_JOB_AGE
from endoreg_db.services.hub.media_integrity import (
    MediaIntegrityResult,
    MediaIntegrityStatus,
)
from endoreg_db.services.hub.ingest import (
    UploadJobIdempotencyConflict,
    create_or_reuse_upload_job,
)


@pytest.fixture
def center(db: None) -> Center:
    return Center.objects.create(name="Operation Identity Center")


def _request(center: Center, content_hash: str) -> tuple[UploadJob, bool]:
    return create_or_reuse_upload_job(
        uploaded_file=None,
        source_center=center,
        content_type="video/mp4",
        content_hash=content_hash,
        idempotency_key="logical-upload",
    )


def test_exact_retry_preserves_scheduled_job_and_retry_budget(center: Center) -> None:
    first, created = _request(center, "a" * 64)
    assert created
    first.schedule_retry(
        "Processing interrupted",
        error_code=UploadJob.ErrorCode.PROCESSING_FAILED,
        delay_seconds=60,
        max_retries=3,
    )
    original_next_retry = first.next_retry_at
    second, created = _request(center, "a" * 64)
    assert not created
    assert second.pk == first.pk
    assert second.status == UploadJob.Status.RETRYING
    assert second.retry_count == 1
    assert second.next_retry_at == original_next_retry
    assert UploadJob.objects.count() == 1


@pytest.mark.parametrize("terminal", [False, True])
def test_changed_content_conflicts_even_after_failure(
    center: Center, terminal: bool
) -> None:
    first, _ = _request(center, "a" * 64)
    if terminal:
        first.mark_error("Processing failed")
    with pytest.raises(UploadJobIdempotencyConflict, match="different content"):
        _request(center, "b" * 64)
    first.refresh_from_db()
    assert first.content_hash == "a" * 64
    assert UploadJob.objects.count() == 1


def test_same_key_in_another_center_never_returns_first_center_job(
    center: Center,
) -> None:
    first, _ = _request(center, "a" * 64)
    other_center = Center.objects.create(name="Independent Operation Center")
    second, created = _request(other_center, "b" * 64)
    assert created
    assert second.pk != first.pk
    assert second.source_center == other_center


def _deliver_stored(center: Center, content_hash: str) -> tuple[UploadJob, bool]:
    return create_or_reuse_upload_job(
        uploaded_file=SimpleUploadedFile(
            "stale.mp4", b"retained video source", content_type="video/mp4"
        ),
        source_center=center,
        content_type="video/mp4",
        content_hash=content_hash,
    )


def _make_stale(job: UploadJob, **fields: object) -> None:
    UploadJob.objects.filter(pk=job.pk).update(
        updated_at=timezone.now() - STALE_UPLOAD_JOB_AGE - timedelta(minutes=1),
        **fields,
    )


@pytest.mark.parametrize(
    "status", [UploadJob.Status.PENDING, UploadJob.Status.PROCESSING]
)
def test_stale_idle_job_recovers_in_place_and_converges(
    center: Center, status: UploadJob.Status
) -> None:
    first, created = _deliver_stored(center, "c" * 64)
    assert created
    _make_stale(first, status=status)

    second, created = _deliver_stored(center, "c" * 64)
    third, created_again = _deliver_stored(center, "c" * 64)

    assert not created and not created_again
    assert second.pk == third.pk == first.pk
    first.refresh_from_db()
    assert first.status == UploadJob.Status.RETRYING
    assert first.retryable
    assert first.retry_count == 1
    assert first.error_code == UploadJob.ErrorCode.PROCESSING_FAILED
    assert UploadJob.objects.count() == 1


@pytest.mark.parametrize("lease_offset", [timedelta(minutes=5), -timedelta(minutes=5)])
def test_stale_leased_job_stays_with_lease_protocol(
    center: Center, lease_offset: timedelta
) -> None:
    first, _ = _deliver_stored(center, "d" * 64)
    _make_stale(
        first,
        status=UploadJob.Status.PROCESSING,
        processing_lease_owner="worker",
        processing_lease_expires_at=timezone.now() + lease_offset,
        processing_heartbeat_at=timezone.now() - timedelta(minutes=10),
        processing_fencing_token=1,
    )

    second, created = _deliver_stored(center, "d" * 64)

    assert not created
    assert second.pk == first.pk
    first.refresh_from_db()
    assert first.status == UploadJob.Status.PROCESSING
    assert first.processing_lease_owner == "worker"
    assert first.processing_fencing_token == 1
    assert first.retry_count == 0
    assert UploadJob.objects.count() == 1


def test_stale_job_without_retained_source_is_reingested(center: Center) -> None:
    first, _ = _request(center, "e" * 64)
    _make_stale(first)

    second, created = _request(center, "e" * 64)

    assert created
    assert second.pk != first.pk
    first.refresh_from_db()
    assert first.status == UploadJob.Status.ERROR


def test_stale_job_with_exhausted_budget_records_terminal_error(
    center: Center,
) -> None:
    first, _ = _deliver_stored(center, "f" * 64)
    _make_stale(first, retry_count=first.max_retries)

    second, created = _deliver_stored(center, "f" * 64)

    assert created
    assert second.pk != first.pk
    first.refresh_from_db()
    assert first.status == UploadJob.Status.ERROR
    assert first.error_code == UploadJob.ErrorCode.PROCESSING_FAILED
    assert first.retry_count == first.max_retries
    assert UploadJob.objects.count() == 2


def _integrity(status: MediaIntegrityStatus) -> MediaIntegrityResult:
    return MediaIntegrityResult(
        ok=status is MediaIntegrityStatus.OK,
        status=status,
        reason=f"integrity {status.value}",
        content_hash="a" * 64,
        media_pk=None,
    )


@pytest.mark.parametrize(
    ("reviewed", "processed", "expected_status"),
    [
        (MediaIntegrityStatus.OK, MediaIntegrityStatus.OK, UploadJob.Status.ANONYMIZED),
        (
            MediaIntegrityStatus.STATE_NOT_VALIDATED,
            MediaIntegrityStatus.OK,
            UploadJob.Status.ANONYMIZED,
        ),
        (
            MediaIntegrityStatus.STATE_NOT_VALIDATED,
            MediaIntegrityStatus.METADATA_MISSING,
            UploadJob.Status.LOST,
        ),
        (
            MediaIntegrityStatus.ARTIFACT_MISSING,
            MediaIntegrityStatus.ARTIFACT_MISSING,
            UploadJob.Status.LOST,
        ),
    ],
)
def test_completed_job_reuse_accepts_processed_media_awaiting_review(
    center: Center,
    monkeypatch: pytest.MonkeyPatch,
    reviewed: MediaIntegrityStatus,
    processed: MediaIntegrityStatus,
    expected_status: UploadJob.Status,
) -> None:
    calls: list[bool] = []

    def _check(job: UploadJob, *, require_review: bool = True) -> MediaIntegrityResult:
        calls.append(require_review)
        return _integrity(reviewed if require_review else processed)

    monkeypatch.setattr(media_integrity, "check_upload_job_media_integrity", _check)
    first, _ = _request(center, "a" * 64)
    first.mark_completed()

    second, created = _request(center, "a" * 64)

    first.refresh_from_db()
    assert first.status == expected_status
    assert calls[0] is True
    if expected_status is UploadJob.Status.ANONYMIZED:
        assert not created
        assert second.pk == first.pk
        assert UploadJob.objects.count() == 1
    else:
        assert created
        assert second.pk != first.pk
        assert first.processing_provenance["media_integrity_status"] == reviewed.value


@pytest.mark.parametrize(
    ("content_type", "dismissed"),
    [
        pytest.param("application/pdf", False, id="report-owned-by-attempt-lease"),
        pytest.param("video/mp4", True, id="dismissed-video"),
    ],
)
def test_stale_job_outside_retained_video_policy_is_reingested(
    center: Center, content_type: str, dismissed: bool
) -> None:
    def _deliver() -> tuple[UploadJob, bool]:
        return create_or_reuse_upload_job(
            uploaded_file=SimpleUploadedFile(
                "stale.bin", b"retained source", content_type=content_type
            ),
            source_center=center,
            content_type=content_type,
            content_hash="9" * 64,
        )

    first, _ = _deliver()
    _make_stale(
        first,
        status=UploadJob.Status.PROCESSING,
        overview_dismissed_at=timezone.now() if dismissed else None,
    )

    second, created = _deliver()

    assert created
    assert second.pk != first.pk
    first.refresh_from_db()
    assert first.status == UploadJob.Status.ERROR
    assert first.retry_count == 0
