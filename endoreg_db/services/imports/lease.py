"""Shared import lease mechanics; domain adapters own persistence and policy."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from typing import Self, TypeVar

from django.db import close_old_connections, connection
from django.db.models import Model, QuerySet
from django.db.models.functions import Now

from endoreg_db.services.jobs.error_handling import database_recovery_reason

_Model = TypeVar("_Model", bound=Model)
_Owner = TypeVar("_Owner")


def database_now(subject: QuerySet[_Model]) -> datetime:
    """Read the database clock for exactly one existing ownership subject."""
    value: object = (
        subject.annotate(database_now=Now())
        .values_list("database_now", flat=True)
        .get()
    )
    if not isinstance(value, datetime):
        raise RuntimeError("Database did not return a typed current timestamp")
    return value


def owns_live_lease(
    *,
    current_owner: _Owner,
    expected_owner: _Owner,
    current_token: int,
    expected_token: int,
    expires_at: datetime | None,
    now: datetime,
) -> bool:
    return (
        current_owner == expected_owner
        and current_token == expected_token
        and expires_at is not None
        and expires_at > now
    )


class ImportLeaseHeartbeat:
    """Renew on a worker connection; surface failure before the next mutation.

    Connection cleanup belongs to the renewal
    thread, never to the caller. Domain callbacks retain cancellation checks,
    structured events, and their own ownership-loss exception types.
    """

    def __init__(
        self,
        *,
        renew: Callable[[], None],
        lost_error: type[RuntimeError],
        name: str,
        interval_seconds: float,
        on_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Import heartbeat interval must be positive")
        self._renew = renew
        self._lost_error = lost_error
        self._on_failure = on_failure
        self._interval_seconds = interval_seconds
        self._failure: BaseException | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def __enter__(self) -> Self:
        if not connection.get_autocommit():
            raise RuntimeError(
                "Import heartbeat requires autocommit; an outer transaction "
                "hides the lease from the renewal connection."
            )
        self._thread.start()
        return self

    def guard(self) -> None:
        if self._failure is not None:
            if database_recovery_reason(self._failure) is not None:
                raise self._failure
            raise self._lost_error("Import heartbeat failed") from self._failure
        self._renew()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self._stop.set()
        self._thread.join(timeout=min(5.0, self._interval_seconds))

    def _run(self) -> None:
        try:
            close_old_connections()
            while not self._stop.wait(self._interval_seconds):
                self._renew()
        except BaseException as exc:
            self._failure = exc
            if self._on_failure is not None:
                self._on_failure(exc)
        finally:
            close_old_connections()
