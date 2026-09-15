from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from endoreg_db.utils.rust_backend import (
    transition_operation_lifecycle as transition_operation_lifecycle_native,
)
from endoreg_db.utils.rust_backend import (
    transition_service_lifecycle as transition_service_lifecycle_native,
)


class ServiceLifecycleState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    FAILED = "failed"
    LOST = "lost"


class ServiceLifecycleEvent(StrEnum):
    START_REQUESTED = "start_requested"
    START_SUCCEEDED = "start_succeeded"
    START_FAILED = "start_failed"
    HEALTH_DEGRADED = "health_degraded"
    HEALTH_RESTORED = "health_restored"
    STOP_REQUESTED = "stop_requested"
    STOP_SUCCEEDED = "stop_succeeded"
    STOP_FAILED = "stop_failed"
    RUNTIME_FAILED = "runtime_failed"
    OWNERSHIP_LOST = "ownership_lost"
    RECONCILE_STOPPED = "reconcile_stopped"


class OperationLifecycleState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


class OperationLifecycleEvent(StrEnum):
    CLAIM = "claim"
    START = "start"
    SUCCEED = "succeed"
    FAIL = "fail"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_READY = "retry_ready"
    RETRY_REQUESTED = "retry_requested"
    CANCEL = "cancel"
    OWNERSHIP_LOST = "ownership_lost"
    INTEGRITY_LOST = "integrity_lost"
    RECONCILE_RETRY = "reconcile_retry"
    RECONCILE_FAIL = "reconcile_fail"


def transition_service_lifecycle(
    current_state: ServiceLifecycleState,
    event: ServiceLifecycleEvent,
) -> ServiceLifecycleState:
    """Apply one native, side-effect-free service lifecycle transition."""
    target = transition_service_lifecycle_native(
        current_state=current_state.value,
        event=event.value,
    )
    return ServiceLifecycleState(target)


def transition_operation_lifecycle(
    current_state: OperationLifecycleState,
    event: OperationLifecycleEvent,
) -> OperationLifecycleState:
    """Apply one native, side-effect-free bounded-operation transition."""
    target = transition_operation_lifecycle_native(
        current_state=current_state.value,
        event=event.value,
    )
    return OperationLifecycleState(target)


def reduce_operation_lifecycle(
    current_state: OperationLifecycleState,
    events: Iterable[OperationLifecycleEvent],
) -> OperationLifecycleState:
    """Apply an ordered domain-event path through the native reducer."""
    state = current_state
    for event in events:
        state = transition_operation_lifecycle(state, event)
    return state
