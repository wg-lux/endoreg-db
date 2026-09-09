from __future__ import annotations

import pytest

from endoreg_db.models.administration.center.center import Center
from endoreg_db.models.hub.upload_job import UploadJob
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
