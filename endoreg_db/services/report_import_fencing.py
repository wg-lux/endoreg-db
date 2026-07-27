from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from collections.abc import Generator
from typing import Final

from django.db import transaction
from django.utils import timezone

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt

DEFAULT_REPORT_IMPORT_LEASE_SECONDS: Final = 30 * 60


class ReportImportBusyError(RuntimeError):
    """Raised when another non-expired attempt owns the content hash."""


class StaleReportImportAttemptError(RuntimeError):
    """Raised when an attempt's fencing token no longer owns the content hash."""


@dataclass(frozen=True, slots=True)
class ReportImportFence:
    content_hash: str
    owner_id: uuid.UUID
    fencing_token: int


def _validate_content_hash(content_hash: str) -> str:
    normalized = content_hash.strip()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("content_hash must be a lowercase SHA-256 digest")
    return normalized


def _require_owner(
    attempt: ReportImportAttempt,
    fence: ReportImportFence,
) -> None:
    if (
        attempt.status != ReportImportAttempt.STATUS_ACTIVE
        or attempt.owner_id != fence.owner_id
        or int(attempt.fencing_token) != fence.fencing_token
    ):
        raise StaleReportImportAttemptError(
            "Report import attempt was superseded "
            f"(content_hash={fence.content_hash}, token={fence.fencing_token})."
        )


def acquire_report_import_fence(
    content_hash: str,
    *,
    lease_seconds: int = DEFAULT_REPORT_IMPORT_LEASE_SECONDS,
) -> ReportImportFence:
    normalized_hash = _validate_content_hash(content_hash)
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    owner_id = uuid.uuid4()
    now = timezone.now()
    with transaction.atomic():
        attempt, _created = (
            ReportImportAttempt.objects.select_for_update().get_or_create(
                content_hash=normalized_hash
            )
        )
        if (
            attempt.status == ReportImportAttempt.STATUS_ACTIVE
            and attempt.lease_expires_at is not None
            and attempt.lease_expires_at > now
        ):
            raise ReportImportBusyError(
                "Another report import attempt holds a non-expired lease "
                f"for content_hash={normalized_hash}."
            )
        attempt.fencing_token = int(attempt.fencing_token) + 1
        attempt.owner_id = owner_id
        attempt.status = ReportImportAttempt.STATUS_ACTIVE
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.save(
            update_fields=[
                "fencing_token",
                "owner_id",
                "status",
                "heartbeat_at",
                "lease_expires_at",
                "updated_at",
            ]
        )
    return ReportImportFence(
        content_hash=normalized_hash,
        owner_id=owner_id,
        fencing_token=int(attempt.fencing_token),
    )


def renew_report_import_fence(
    fence: ReportImportFence,
    *,
    lease_seconds: int = DEFAULT_REPORT_IMPORT_LEASE_SECONDS,
) -> None:
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")
    now = timezone.now()
    with transaction.atomic():
        attempt = ReportImportAttempt.objects.select_for_update().get(
            content_hash=fence.content_hash
        )
        _require_owner(attempt, fence)
        attempt.heartbeat_at = now
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.save(update_fields=["heartbeat_at", "lease_expires_at", "updated_at"])


@contextmanager
def report_import_finalization_guard(
    fence: ReportImportFence,
) -> Generator[None]:
    """Hold the database row lock while the current owner publishes success."""
    with transaction.atomic():
        attempt = ReportImportAttempt.objects.select_for_update().get(
            content_hash=fence.content_hash
        )
        _require_owner(attempt, fence)
        yield
        attempt.status = ReportImportAttempt.STATUS_SUCCEEDED
        attempt.owner_id = None
        attempt.heartbeat_at = None
        attempt.lease_expires_at = None
        attempt.save(
            update_fields=[
                "status",
                "owner_id",
                "heartbeat_at",
                "lease_expires_at",
                "updated_at",
            ]
        )


def mark_report_import_fence_failed(fence: ReportImportFence) -> bool:
    """Release this attempt only if its token still owns the row."""
    updated = ReportImportAttempt.objects.filter(
        content_hash=fence.content_hash,
        fencing_token=fence.fencing_token,
        owner_id=fence.owner_id,
        status=ReportImportAttempt.STATUS_ACTIVE,
    ).update(
        status=ReportImportAttempt.STATUS_FAILED,
        owner_id=None,
        heartbeat_at=None,
        lease_expires_at=None,
        updated_at=timezone.now(),
    )
    return updated == 1
