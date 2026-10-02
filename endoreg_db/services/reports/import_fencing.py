from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from django.db import transaction
from django.db.models.functions import Now

from endoreg_db.services.imports.lease import (
    ImportLeaseHeartbeat,
    database_now,
    owns_live_lease,
)

from endoreg_db.models.state.report_import_attempt import ReportImportAttempt
from endoreg_db.services.reports.import_state_machine import (
    validate_report_import_claim,
    validate_report_import_failure,
    validate_report_import_ownership_lost,
    validate_report_import_success,
)

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


def _database_now(content_hash: str) -> datetime:
    return database_now(ReportImportAttempt.objects.filter(content_hash=content_hash))


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
    *,
    database_now: datetime,
) -> None:
    if attempt.status != ReportImportAttempt.STATUS_ACTIVE or not owns_live_lease(
        current_owner=attempt.owner_id,
        expected_owner=fence.owner_id,
        current_token=int(attempt.fencing_token),
        expected_token=fence.fencing_token,
        expires_at=attempt.lease_expires_at,
        now=database_now,
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
    with transaction.atomic():
        expired_attempt = (
            ReportImportAttempt.objects.select_for_update()
            .filter(content_hash=normalized_hash)
            .first()
        )
        if expired_attempt is not None:
            now = _database_now(normalized_hash)
            if (
                expired_attempt.status == ReportImportAttempt.STATUS_ACTIVE
                and expired_attempt.lease_expires_at is not None
                and expired_attempt.lease_expires_at <= now
            ):
                validate_report_import_ownership_lost(
                    current_status=expired_attempt.status
                )
                expired_attempt.status = ReportImportAttempt.STATUS_LOST
                expired_attempt.owner_id = None
                expired_attempt.heartbeat_at = None
                expired_attempt.lease_expires_at = None
                expired_attempt.save(
                    update_fields=[
                        "status",
                        "owner_id",
                        "heartbeat_at",
                        "lease_expires_at",
                        "updated_at",
                    ]
                )

    owner_id = uuid.uuid4()
    with transaction.atomic():
        attempt, _created = (
            ReportImportAttempt.objects.select_for_update().get_or_create(
                content_hash=normalized_hash
            )
        )
        now = _database_now(normalized_hash)
        if (
            attempt.status == ReportImportAttempt.STATUS_ACTIVE
            and attempt.lease_expires_at is not None
            and attempt.lease_expires_at > now
        ):
            raise ReportImportBusyError(
                "Another report import attempt holds a non-expired lease "
                f"for content_hash={normalized_hash}."
            )
        validate_report_import_claim(current_status=attempt.status, interrupted=False)
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
    with transaction.atomic():
        attempt = ReportImportAttempt.objects.select_for_update().get(
            content_hash=fence.content_hash
        )
        now = _database_now(fence.content_hash)
        _require_owner(attempt, fence, database_now=now)
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
        _require_owner(
            attempt,
            fence,
            database_now=_database_now(fence.content_hash),
        )
        yield
        validate_report_import_success(current_status=attempt.status)
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


@contextmanager
def report_import_mutation_guard(
    fence: ReportImportFence,
) -> Generator[None]:
    """Fence one report metadata mutation in the attempt-row transaction."""
    with transaction.atomic():
        attempt = ReportImportAttempt.objects.select_for_update().get(
            content_hash=fence.content_hash
        )
        _require_owner(
            attempt,
            fence,
            database_now=_database_now(fence.content_hash),
        )
        yield


def mark_report_import_fence_failed(fence: ReportImportFence) -> bool:
    """Release this attempt only if its token still owns the row."""
    validate_report_import_failure(
        current_status=ReportImportAttempt.STATUS_ACTIVE,
    )
    updated = ReportImportAttempt.objects.filter(
        content_hash=fence.content_hash,
        fencing_token=fence.fencing_token,
        owner_id=fence.owner_id,
        status=ReportImportAttempt.STATUS_ACTIVE,
        lease_expires_at__gt=Now(),
    ).update(
        status=ReportImportAttempt.STATUS_FAILED,
        owner_id=None,
        heartbeat_at=None,
        lease_expires_at=None,
        updated_at=Now(),
    )
    return updated == 1


class ReportImportFenceHeartbeat(ImportLeaseHeartbeat):
    """Report-content renewal with the shared connection and recovery contract."""

    def __init__(
        self,
        fence: ReportImportFence,
        *,
        interval_seconds: float | None = None,
    ) -> None:
        super().__init__(
            renew=lambda: renew_report_import_fence(fence),
            lost_error=StaleReportImportAttemptError,
            name=f"report-import-heartbeat-{fence.owner_id}",
            interval_seconds=(
                interval_seconds
                if interval_seconds is not None
                else max(10.0, min(60.0, DEFAULT_REPORT_IMPORT_LEASE_SECONDS / 3))
            ),
        )
