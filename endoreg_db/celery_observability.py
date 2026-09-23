from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Literal, cast

from celery.signals import task_failure, task_postrun, task_prerun

from endoreg_db.utils.structured_logging import emit_structured_event, hash_identifier

from endoreg_db.utils.workload_timing import (
    WorkloadOperation,
    WorkloadOutcome,
    emit_workload_timing,
    normalize_task_family,
    normalize_workload_queue,
    retry_bucket,
    start_workload_timing,
)


logger = logging.getLogger("endoreg_db.workload_timing")

_TASK_PRERUN_DISPATCH_UID = "endoreg_db.workload_timing.task_prerun"
_TASK_POSTRUN_DISPATCH_UID = "endoreg_db.workload_timing.task_postrun"
_TASK_FAILURE_DISPATCH_UID = "endoreg_db.workload_timing.task_failure"
_MAX_ACTIVE_TASKS = 4096

_active_task_started_at: dict[str, float] = {}
_active_task_lock = threading.Lock()
_registration_lock = threading.Lock()
_signals_registered = False


def _task_name(task: object | None, sender: object | None) -> object:
    task_name = getattr(task, "name", None)
    if task_name is not None:
        return task_name
    return getattr(sender, "name", None)


def _task_queue(task: object | None) -> object:
    request = getattr(task, "request", None)
    delivery_info = getattr(request, "delivery_info", None)
    if not isinstance(delivery_info, Mapping):
        return None
    typed_delivery_info = cast(Mapping[object, object], delivery_info)
    return typed_delivery_info.get("routing_key") or typed_delivery_info.get("exchange")


def _task_retries(task: object | None) -> object:
    request = getattr(task, "request", None)
    return getattr(request, "retries", None)


def _outcome_from_celery_state(state: object) -> WorkloadOutcome:
    normalized = str(state or "").strip().upper()
    outcomes = {
        "SUCCESS": WorkloadOutcome.COMPLETED,
        "FAILURE": WorkloadOutcome.FAILED,
        "RETRY": WorkloadOutcome.RETRY,
        "REVOKED": WorkloadOutcome.REVOKED,
    }
    return outcomes.get(normalized, WorkloadOutcome.UNKNOWN)


def _remember_task_start(task_id: object) -> None:
    if not isinstance(task_id, str) or not task_id:
        return
    started_at = start_workload_timing()
    with _active_task_lock:
        if len(_active_task_started_at) >= _MAX_ACTIVE_TASKS:
            oldest_task_id = next(iter(_active_task_started_at))
            _active_task_started_at.pop(oldest_task_id, None)
        _active_task_started_at[task_id] = started_at


def _take_task_start(task_id: object) -> float | None:
    if not isinstance(task_id, str) or not task_id:
        return None
    with _active_task_lock:
        return _active_task_started_at.pop(task_id, None)


def _task_prerun_receiver(
    sender: object | None = None,
    task_id: object | None = None,
    task: object | None = None,
    **_ignored: object,
) -> None:
    _remember_task_start(task_id)


def _task_postrun_receiver(
    sender: object | None = None,
    task_id: object | None = None,
    task: object | None = None,
    state: object | None = None,
    **_ignored: object,
) -> None:
    started_at = _take_task_start(task_id)
    if started_at is None:
        return
    effective_task = task if task is not None else sender
    emit_workload_timing(
        logger,
        started_at=started_at,
        operation=WorkloadOperation.CELERY_TASK,
        outcome=_outcome_from_celery_state(state),
        task_family=normalize_task_family(_task_name(effective_task, sender)),
        queue=normalize_workload_queue(_task_queue(effective_task)),
        retry=retry_bucket(_task_retries(effective_task)),
    )


def _failure_classification(
    exception: object,
) -> Literal[
    "timeout", "connection", "permission", "invalid_value", "os_error", "unknown"
]:
    # Only fixed categories are public: exception names and messages can carry
    # clinical input, including names synthesized by third-party integrations.
    if isinstance(exception, TimeoutError):
        return "timeout"
    if isinstance(exception, ConnectionError):
        return "connection"
    if isinstance(exception, PermissionError):
        return "permission"
    if isinstance(exception, ValueError):
        return "invalid_value"
    if isinstance(exception, OSError):
        return "os_error"
    return "unknown"


def _task_failure_receiver(
    sender: object | None = None,
    task_id: object | None = None,
    exception: object | None = None,
    **_ignored: object,
) -> None:
    """Report failure even when a delivery never emitted a start signal."""
    emit_structured_event(
        logger,
        "celery.task_failure",
        level=logging.ERROR,
        operation=WorkloadOperation.CELERY_TASK.value,
        outcome=WorkloadOutcome.FAILED.value,
        task_id_sha256=(
            hash_identifier(task_id) if isinstance(task_id, str) and task_id else None
        ),
        task_family=normalize_task_family(_task_name(None, sender)).value,
        queue=normalize_workload_queue(_task_queue(sender)).value,
        retry_bucket=retry_bucket(_task_retries(sender)).value,
        error_classification=_failure_classification(exception),
    )


def register_celery_timing_signals() -> None:
    """Register global Celery timing and failure hooks once per process."""
    global _signals_registered

    with _registration_lock:
        if _signals_registered:
            return
        task_prerun.connect(
            _task_prerun_receiver,
            weak=False,
            dispatch_uid=_TASK_PRERUN_DISPATCH_UID,
        )
        task_postrun.connect(
            _task_postrun_receiver,
            weak=False,
            dispatch_uid=_TASK_POSTRUN_DISPATCH_UID,
        )
        task_failure.connect(
            _task_failure_receiver,
            weak=False,
            dispatch_uid=_TASK_FAILURE_DISPATCH_UID,
        )
        _signals_registered = True


__all__ = ["register_celery_timing_signals"]
