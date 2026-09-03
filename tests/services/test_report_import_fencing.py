from __future__ import annotations

from datetime import timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from django.utils import timezone

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.services.report_import_fencing import (
    ReportImportBusyError,
    ReportImportFenceHeartbeat,
    StaleReportImportAttemptError,
    acquire_report_import_fence,
    mark_report_import_fence_failed,
    renew_report_import_fence,
    report_import_finalization_guard,
    report_import_mutation_guard,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_active_attempt_blocks_second_owner() -> None:
    content_hash = "a" * 64
    first = acquire_report_import_fence(content_hash)

    with pytest.raises(ReportImportBusyError):
        acquire_report_import_fence(content_hash)

    assert mark_report_import_fence_failed(first)


def test_expired_attempt_is_fenced_from_finalization() -> None:
    content_hash = "b" * 64
    stale = acquire_report_import_fence(content_hash)
    ReportImportAttempt.objects.filter(content_hash=content_hash).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    current = acquire_report_import_fence(content_hash)

    with (
        pytest.raises(StaleReportImportAttemptError),
        report_import_finalization_guard(stale),
    ):
        pass

    with report_import_finalization_guard(current):
        pass

    attempt = ReportImportAttempt.objects.get(content_hash=content_hash)
    assert attempt.status == ReportImportAttempt.STATUS_SUCCEEDED
    assert attempt.fencing_token == 2


def test_current_owner_can_renew_and_failure_release_is_conditional() -> None:
    content_hash = "c" * 64
    fence = acquire_report_import_fence(content_hash, lease_seconds=1)

    renew_report_import_fence(fence, lease_seconds=60)

    attempt = ReportImportAttempt.objects.get(content_hash=content_hash)
    assert attempt.lease_expires_at is not None
    assert attempt.lease_expires_at > timezone.now() + timedelta(seconds=50)
    assert mark_report_import_fence_failed(fence)
    assert not mark_report_import_fence_failed(fence)


def test_failed_attempt_is_automatically_retried_with_new_fence() -> None:
    content_hash = "1" * 64
    failed = acquire_report_import_fence(content_hash)
    assert mark_report_import_fence_failed(failed)

    retried = acquire_report_import_fence(content_hash)

    attempt = ReportImportAttempt.objects.get(content_hash=content_hash)
    assert retried.fencing_token == failed.fencing_token + 1
    assert attempt.status == ReportImportAttempt.STATUS_ACTIVE
    assert attempt.owner_id == retried.owner_id


def test_succeeded_attempt_accepts_explicit_new_import_attempt() -> None:
    content_hash = "2" * 64
    completed = acquire_report_import_fence(content_hash)
    with report_import_finalization_guard(completed):
        pass

    retried = acquire_report_import_fence(content_hash)

    attempt = ReportImportAttempt.objects.get(content_hash=content_hash)
    assert retried.fencing_token == completed.fencing_token + 1
    assert attempt.status == ReportImportAttempt.STATUS_ACTIVE


def test_expired_owner_cannot_mutate_report_metadata() -> None:
    content_hash = "d" * 64
    stale = acquire_report_import_fence(content_hash)
    ReportImportAttempt.objects.filter(content_hash=content_hash).update(
        lease_expires_at=timezone.now() - timedelta(seconds=1)
    )

    with (
        pytest.raises(StaleReportImportAttemptError),
        report_import_mutation_guard(stale),
    ):
        pytest.fail("stale attempt entered the mutation boundary")


def test_background_heartbeat_renews_during_long_running_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = acquire_report_import_fence("e" * 64)
    renewed = Event()
    original_renew = renew_report_import_fence

    def renew_and_signal(*args: object, **kwargs: object) -> None:
        original_renew(fence, lease_seconds=60)
        renewed.set()

    monkeypatch.setattr(
        "endoreg_db.services.report_import_fencing.renew_report_import_fence",
        Mock(side_effect=renew_and_signal),
    )
    with ReportImportFenceHeartbeat(fence, interval_seconds=0.01):
        assert renewed.wait(timeout=1)


def test_background_heartbeat_failure_is_raised_at_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fence = acquire_report_import_fence("f" * 64)
    failed = Event()

    def reject_renewal(*args: object, **kwargs: object) -> None:
        failed.set()
        raise StaleReportImportAttemptError("superseded")

    monkeypatch.setattr(
        "endoreg_db.services.report_import_fencing.renew_report_import_fence",
        reject_renewal,
    )
    with ReportImportFenceHeartbeat(fence, interval_seconds=0.01) as heartbeat:
        assert failed.wait(timeout=1)
        with pytest.raises(StaleReportImportAttemptError, match="heartbeat failed"):
            heartbeat.guard()
