from __future__ import annotations

from collections.abc import Callable, Sequence
import logging
from typing import NoReturn, Protocol, cast

from django.apps import apps
from django.db import DatabaseError, InterfaceError, OperationalError

from endoreg_db.config.env import get_video_post_validation_dispatch_delay_seconds
from endoreg_db.exceptions import MediaOperationDeferred, describe_job_error
from endoreg_db.utils.structured_logging import emit_structured_event, hash_identifier


logger = logging.getLogger("endoreg_db.jobs")

RetryCallable = Callable[..., BaseException]


class _Column(Protocol):
    column: str | None


class _ModelColumns(Protocol):
    local_fields: Sequence[_Column]


def database_recovery_reason(error: BaseException) -> str | None:
    """Classify driver metadata without parsing or exposing SQL/patient values."""
    if isinstance(error, (OperationalError, InterfaceError)):
        return "database_unavailable"
    if not isinstance(error, DatabaseError):
        return None
    cause = error.__cause__
    sqlstate = getattr(cause, "sqlstate", None)
    if sqlstate in ("42P01", "42703"):
        return "database_schema_mismatch"
    if sqlstate == "23502":
        diagnostic = getattr(cause, "diag", None)
        table: object = getattr(diagnostic, "table_name", None)
        column: object = getattr(diagnostic, "column_name", None)
        if isinstance(table, str) and isinstance(column, str):
            for model in apps.get_app_config("endoreg_db").get_models():
                if model._meta.db_table == table:
                    fields = cast(_ModelColumns, model._meta).local_fields
                    if column not in {field.column for field in fields}:
                        return "database_schema_mismatch"
                    break
    return None


def retry_database_operation(
    *,
    retry: RetryCallable,
    error: DatabaseError,
    retries: int,
    job_name: str,
    subject_id: object,
) -> NoReturn:
    """Wait indefinitely for repair, with a bounded rate and no payload leakage."""
    reason = database_recovery_reason(error)
    if reason is None:
        raise error
    countdown = min(900, 60 * 2 ** min(max(retries, 0), 4))
    emit_structured_event(
        logger,
        "job.retry_scheduled",
        job_name=job_name,
        subject_id_sha256=hash_identifier(subject_id),
        error_code=reason,
        retryable=True,
        countdown_seconds=countdown,
        max_retries=None,
    )
    # Celery persists this exception; the driver's text can contain a failing row.
    raise retry(
        exc=RuntimeError(reason),
        countdown=countdown,
        max_retries=None,
        throw=False,
    ) from None


def retry_deferred_media_operation(
    *,
    retry: RetryCallable,
    error: MediaOperationDeferred,
    job_name: str,
    video_id: object,
) -> NoReturn:
    """Apply the shared Celery retry and audit policy for deferred media work."""

    descriptor = describe_job_error(error)
    if not descriptor.retryable:
        raise error
    countdown = max(
        get_video_post_validation_dispatch_delay_seconds(),
        descriptor.minimum_countdown_seconds,
    )
    emit_structured_event(
        logger,
        "job.retry_scheduled",
        job_name=job_name,
        subject_id_sha256=hash_identifier(video_id),
        error_code=descriptor.code.value,
        reason=descriptor.log_reason,
        retryable=descriptor.retryable,
        countdown_seconds=countdown,
        max_retries=descriptor.max_retries,
    )
    raise retry(
        exc=error,
        countdown=countdown,
        max_retries=descriptor.max_retries,
    ) from error


__all__ = [
    "database_recovery_reason",
    "retry_database_operation",
    "retry_deferred_media_operation",
]
