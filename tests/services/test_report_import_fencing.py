from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.services.report_import_fencing import (
    ReportImportBusyError,
    StaleReportImportAttemptError,
    acquire_report_import_fence,
    mark_report_import_fence_failed,
    renew_report_import_fence,
    report_import_finalization_guard,
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

    with pytest.raises(StaleReportImportAttemptError):
        with report_import_finalization_guard(stale):
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
