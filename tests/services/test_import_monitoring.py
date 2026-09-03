from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from endoreg_db.models import UploadJob
from endoreg_db.services.hub.import_monitoring import (
    dispatch_due_upload_job_retries,
    retry_delay_seconds,
    safe_import_error_detail,
    schedule_processing_retry,
)


def test_retry_delay_is_bounded_exponential_backoff() -> None:
    assert [retry_delay_seconds(value) for value in (0, 1, 2, 10)] == [
        30,
        60,
        120,
        900,
    ]


def test_safe_error_detail_does_not_echo_technical_input() -> None:
    assert safe_import_error_detail("unknown /protected/secret.mp4") == ""
    assert "/protected" not in safe_import_error_detail(
        UploadJob.ErrorCode.DISPATCH_UNAVAILABLE
    )


@pytest.mark.django_db
def test_due_retry_is_dispatched_once_and_marked_processing() -> None:
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile("retry.pdf", b"%PDF-1.4\n%%EOF\n"),
        content_type="application/pdf",
    )
    upload_job.schedule_retry(
        "broker unavailable",
        error_code=UploadJob.ErrorCode.DISPATCH_UNAVAILABLE,
        delay_seconds=30,
    )
    UploadJob.objects.filter(pk=upload_job.pk).update(next_retry_at=timezone.now())
    dispatcher = Mock()

    result = dispatch_due_upload_job_retries(
        dispatcher=dispatcher,
        queue="pipeline",
    )
    upload_job.refresh_from_db()

    assert result.due_count == 1
    assert result.dispatched_count == 1
    assert result.failed_count == 0
    assert upload_job.status == UploadJob.Status.PROCESSING
    assert upload_job.last_attempt_at is not None
    dispatcher.apply_async.assert_called_once_with(
        args=(str(upload_job.pk),),
        queue="pipeline",
        routing_key="pipeline",
    )


@pytest.mark.django_db
def test_processing_failure_is_scheduled_through_native_retry_state() -> None:
    upload_job = UploadJob.objects.create(
        file=SimpleUploadedFile("retry.mp4", b"video"),
        content_type="video/mp4",
        status=UploadJob.Status.PROCESSING,
    )

    scheduled = schedule_processing_retry(
        upload_job,
        technical_detail="worker interrupted",
    )

    upload_job.refresh_from_db()
    assert scheduled is True
    assert upload_job.status == UploadJob.Status.RETRYING
    assert upload_job.retryable is True
    assert upload_job.next_retry_at is not None
