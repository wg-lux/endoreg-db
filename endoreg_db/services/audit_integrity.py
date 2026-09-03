from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from endoreg_db.models.state.audit_ledger import AuditLedger, LedgerHead
from endoreg_db.utils.structured_logging import (
    emit_structured_event,
    safe_log_value,
)


AUDIT_LEDGER_INTEGRITY_CACHE_KEY = "audit-ledger:integrity-status:v1"
AUDIT_LEDGER_INTEGRITY_LOCK_KEY = "audit-ledger:integrity-refresh-lock:v1"
AUDIT_LEDGER_INTEGRITY_LOCK_TIMEOUT_SECONDS = 60 * 30
logger = logging.getLogger(__name__)


def _ledger_head_snapshot() -> dict[str, Any]:
    head = LedgerHead.objects.first()
    if head is None:
        return {
            "ledger_head_hash": "0" * 64,
            "last_entry_id": None,
        }
    return {
        "ledger_head_hash": head.current_hash,
        "last_entry_id": str(head.last_entry.pk)
        if head.last_entry is not None
        else None,
    }


def get_audit_ledger_integrity_status() -> dict[str, Any]:
    """
    Return the cached ledger integrity status without running a full chain scan.

    The frontend can use `verified` for the green tick. A missing cache is
    reported as `unknown`, not as verified.
    """
    cached = cache.get(AUDIT_LEDGER_INTEGRITY_CACHE_KEY)
    if isinstance(cached, dict):
        return {**cached, "source": "cache"}

    return {
        "status": "unknown",
        "verified": False,
        "checked_at": None,
        "entry_count": None,
        "error": None,
        "source": "default",
        **_ledger_head_snapshot(),
    }


def refresh_audit_ledger_integrity_status() -> dict[str, Any]:
    """
    Run the expensive O(N) chain verification and cache the resulting status.

    This function is intended for Celery beat, management commands, or explicit
    operator-triggered checks, not request-time dashboard rendering.
    """
    checked_at = timezone.now().isoformat()
    try:
        verified = AuditLedger.verify_chain()
        payload = {
            "status": "verified" if verified else "failed",
            "verified": verified,
            "checked_at": checked_at,
            "entry_count": AuditLedger.objects.count(),
            "error": None,
            **_ledger_head_snapshot(),
        }
    except Exception as exc:  # pragma: no cover - defensive operational guard
        payload = {
            "status": "error",
            "verified": False,
            "checked_at": checked_at,
            "entry_count": None,
            "error": str(exc),
            **_ledger_head_snapshot(),
        }

    if payload["status"] == "failed":
        emit_structured_event(
            logger,
            "audit_ledger.integrity_failed",
            level=logging.ERROR,
            entry_count=payload["entry_count"],
            ledger_head_hash=payload["ledger_head_hash"],
            last_entry_id=payload["last_entry_id"],
        )
    elif payload["status"] == "error":
        emit_structured_event(
            logger,
            "audit_ledger.integrity_error",
            level=logging.ERROR,
            error=safe_log_value(payload["error"]),
            ledger_head_hash=payload["ledger_head_hash"],
            last_entry_id=payload["last_entry_id"],
        )

    cache.set(AUDIT_LEDGER_INTEGRITY_CACHE_KEY, payload, timeout=None)
    return {**payload, "source": "refresh"}


def refresh_audit_ledger_integrity_status_once() -> dict[str, Any]:
    """
    Refresh integrity status unless another beat/worker is already doing it.

    This protects the maintenance queue from overlapping O(N) verification runs
    when a previous scan takes longer than the beat interval.
    """
    lock_acquired = cache.add(
        AUDIT_LEDGER_INTEGRITY_LOCK_KEY,
        timezone.now().isoformat(),
        timeout=AUDIT_LEDGER_INTEGRITY_LOCK_TIMEOUT_SECONDS,
    )
    if not lock_acquired:
        return {
            **get_audit_ledger_integrity_status(),
            "source": "skipped_locked",
        }

    try:
        return refresh_audit_ledger_integrity_status()
    finally:
        cache.delete(AUDIT_LEDGER_INTEGRITY_LOCK_KEY)
