from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.core.cache import cache

from endoreg_db.models.state.audit_ledger import AuditLedger, LedgerHead
from endoreg_db.services import audit_integrity
from endoreg_db.services.audit_integrity import (
    AUDIT_LEDGER_INTEGRITY_CACHE_KEY,
    AUDIT_LEDGER_INTEGRITY_LOCK_KEY,
    get_audit_ledger_integrity_status,
    refresh_audit_ledger_integrity_status,
    refresh_audit_ledger_integrity_status_once,
)
from endoreg_db.tasks import (
    process_upload_job,
    refresh_audit_ledger_integrity_status_task,
    run_video_post_validation_rebuild_task,
)


@pytest.fixture(autouse=True)
def clear_integrity_cache():
    cache.delete(AUDIT_LEDGER_INTEGRITY_CACHE_KEY)
    cache.delete(AUDIT_LEDGER_INTEGRITY_LOCK_KEY)
    yield
    cache.delete(AUDIT_LEDGER_INTEGRITY_CACHE_KEY)
    cache.delete(AUDIT_LEDGER_INTEGRITY_LOCK_KEY)


def test_celery_beat_routes_integrity_to_separate_queue():
    route = settings.CELERY_TASK_ROUTES[
        "endoreg_db.refresh_audit_ledger_integrity_status"
    ]
    beat_entry = settings.CELERY_BEAT_SCHEDULE["audit-ledger-integrity-refresh"]

    assert route["queue"] == settings.CELERY_MAINTENANCE_QUEUE
    assert beat_entry["task"] == "endoreg_db.refresh_audit_ledger_integrity_status"
    assert beat_entry["options"]["queue"] == settings.CELERY_MAINTENANCE_QUEUE
    assert (
        settings.CELERY_TASK_ROUTES["endoreg_db.process_upload_job"]["queue"]
        == settings.CELERY_PIPELINE_QUEUE
    )
    assert (
        settings.CELERY_TASK_ROUTES["endoreg_db.video_post_validation_rebuild"]["queue"]
        == settings.CELERY_PIPELINE_QUEUE
    )


@pytest.mark.django_db
def test_status_defaults_to_unknown_without_cache_or_head():
    LedgerHead.objects.all().delete()

    payload = get_audit_ledger_integrity_status()

    assert payload == {
        "status": "unknown",
        "verified": False,
        "checked_at": None,
        "entry_count": None,
        "error": None,
        "source": "default",
        "ledger_head_hash": "0" * 64,
        "last_entry_id": None,
    }


@pytest.mark.django_db
def test_status_ignores_malformed_cache_value():
    cache.set(AUDIT_LEDGER_INTEGRITY_CACHE_KEY, "not-a-status-payload", timeout=None)

    payload = get_audit_ledger_integrity_status()

    assert payload["status"] == "unknown"
    assert payload["source"] == "default"


@pytest.mark.django_db
def test_status_default_includes_current_ledger_head_snapshot():
    entry = AuditLedger.append_identity_commit(
        object_type="SensitiveMeta",
        object_pk="snapshot-1",
        data={"payload_hash": "snapshot", "examination_hash": "exam"},
    )
    assert entry is not None

    payload = get_audit_ledger_integrity_status()

    assert payload["source"] == "default"
    assert payload["ledger_head_hash"] == entry.hash
    assert payload["last_entry_id"] == str(entry.pk)


@pytest.mark.django_db
def test_refresh_caches_bad_status_when_verify_returns_false():
    with patch.object(AuditLedger, "verify_chain", return_value=False):
        payload = refresh_audit_ledger_integrity_status()

    cached = cache.get(AUDIT_LEDGER_INTEGRITY_CACHE_KEY)
    assert payload["status"] == "failed"
    assert payload["verified"] is False
    assert payload["source"] == "refresh"
    assert cached["status"] == "failed"
    assert cached["verified"] is False


@pytest.mark.django_db
def test_refresh_caches_error_status_when_verify_errors():
    with patch.object(AuditLedger, "verify_chain", side_effect=RuntimeError("boom")):
        payload = refresh_audit_ledger_integrity_status()

    cached = cache.get(AUDIT_LEDGER_INTEGRITY_CACHE_KEY)
    assert payload["status"] == "error"
    assert payload["verified"] is False
    assert payload["error"] == "boom"
    assert cached["status"] == "error"


def test_refresh_once_skips_when_lock_exists():
    cache.set(
        AUDIT_LEDGER_INTEGRITY_CACHE_KEY,
        {
            "status": "verified",
            "verified": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "entry_count": 1,
            "error": None,
            "ledger_head_hash": "c" * 64,
            "last_entry_id": "entry-3",
        },
        timeout=None,
    )
    cache.set(AUDIT_LEDGER_INTEGRITY_LOCK_KEY, "locked", timeout=60)

    with patch.object(
        audit_integrity,
        "refresh_audit_ledger_integrity_status",
        side_effect=AssertionError("locked refresh must not run"),
    ):
        payload = refresh_audit_ledger_integrity_status_once()

    assert payload["status"] == "verified"
    assert payload["source"] == "skipped_locked"


def test_refresh_once_releases_lock_after_success():
    refresh = Mock(return_value={"status": "verified", "source": "refresh"})

    with patch.object(
        audit_integrity, "refresh_audit_ledger_integrity_status", refresh
    ):
        payload = refresh_audit_ledger_integrity_status_once()

    assert payload == {"status": "verified", "source": "refresh"}
    assert refresh.call_count == 1
    assert cache.get(AUDIT_LEDGER_INTEGRITY_LOCK_KEY) is None


def test_refresh_once_releases_lock_after_exception():
    refresh = Mock(side_effect=RuntimeError("scan failed"))

    with (
        patch.object(audit_integrity, "refresh_audit_ledger_integrity_status", refresh),
        pytest.raises(RuntimeError, match="scan failed"),
    ):
        refresh_audit_ledger_integrity_status_once()

    assert cache.get(AUDIT_LEDGER_INTEGRITY_LOCK_KEY) is None


def test_refresh_task_delegates_to_locked_refresh():
    with patch(
        "endoreg_db.services.audit_integrity.refresh_audit_ledger_integrity_status_once",
        return_value={"status": "verified"},
    ) as refresh:
        payload = refresh_audit_ledger_integrity_status_task.run()

    assert payload == {"status": "verified"}
    refresh.assert_called_once_with()


def test_video_post_validation_task_delegates_to_runner():
    with patch(
        "endoreg_db.services.video_post_validation_jobs._run_video_post_validation_rebuild",
        return_value=True,
    ) as runner:
        result = run_video_post_validation_rebuild_task.run(
            "42",
            only_validated=1,
        )

    assert result is True
    runner.assert_called_once_with(42, only_validated=True, history_id=None)


def test_upload_processing_task_delegates_to_hub_service():
    with patch(
        "endoreg_db.services.hub.process_upload_job",
        return_value=True,
    ) as processor:
        result = process_upload_job.run(123)

    assert result is True
    processor.assert_called_once_with("123")
