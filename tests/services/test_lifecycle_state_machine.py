from __future__ import annotations

import pytest

from endoreg_db.services.lifecycle_state_machine import (
    OperationLifecycleEvent,
    OperationLifecycleState,
    ServiceLifecycleEvent,
    ServiceLifecycleState,
    transition_operation_lifecycle,
    transition_service_lifecycle,
)
from endoreg_db.utils.rust_backend import has_native_capability


SERVICE_TRANSITIONS = {
    (
        ServiceLifecycleState.STOPPED,
        ServiceLifecycleEvent.START_REQUESTED,
    ): ServiceLifecycleState.STARTING,
    (
        ServiceLifecycleState.STOPPED,
        ServiceLifecycleEvent.STOP_SUCCEEDED,
    ): ServiceLifecycleState.STOPPED,
    (
        ServiceLifecycleState.STOPPED,
        ServiceLifecycleEvent.RECONCILE_STOPPED,
    ): ServiceLifecycleState.STOPPED,
    (
        ServiceLifecycleState.STARTING,
        ServiceLifecycleEvent.START_REQUESTED,
    ): ServiceLifecycleState.STARTING,
    (
        ServiceLifecycleState.STARTING,
        ServiceLifecycleEvent.START_SUCCEEDED,
    ): ServiceLifecycleState.RUNNING,
    (
        ServiceLifecycleState.STARTING,
        ServiceLifecycleEvent.START_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.STARTING,
        ServiceLifecycleEvent.STOP_REQUESTED,
    ): ServiceLifecycleState.STOPPING,
    (
        ServiceLifecycleState.STARTING,
        ServiceLifecycleEvent.RUNTIME_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.STARTING,
        ServiceLifecycleEvent.OWNERSHIP_LOST,
    ): ServiceLifecycleState.LOST,
    (
        ServiceLifecycleState.RUNNING,
        ServiceLifecycleEvent.START_SUCCEEDED,
    ): ServiceLifecycleState.RUNNING,
    (
        ServiceLifecycleState.RUNNING,
        ServiceLifecycleEvent.HEALTH_DEGRADED,
    ): ServiceLifecycleState.DEGRADED,
    (
        ServiceLifecycleState.RUNNING,
        ServiceLifecycleEvent.HEALTH_RESTORED,
    ): ServiceLifecycleState.RUNNING,
    (
        ServiceLifecycleState.RUNNING,
        ServiceLifecycleEvent.STOP_REQUESTED,
    ): ServiceLifecycleState.STOPPING,
    (
        ServiceLifecycleState.RUNNING,
        ServiceLifecycleEvent.RUNTIME_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.RUNNING,
        ServiceLifecycleEvent.OWNERSHIP_LOST,
    ): ServiceLifecycleState.LOST,
    (
        ServiceLifecycleState.DEGRADED,
        ServiceLifecycleEvent.HEALTH_DEGRADED,
    ): ServiceLifecycleState.DEGRADED,
    (
        ServiceLifecycleState.DEGRADED,
        ServiceLifecycleEvent.HEALTH_RESTORED,
    ): ServiceLifecycleState.RUNNING,
    (
        ServiceLifecycleState.DEGRADED,
        ServiceLifecycleEvent.STOP_REQUESTED,
    ): ServiceLifecycleState.STOPPING,
    (
        ServiceLifecycleState.DEGRADED,
        ServiceLifecycleEvent.RUNTIME_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.DEGRADED,
        ServiceLifecycleEvent.OWNERSHIP_LOST,
    ): ServiceLifecycleState.LOST,
    (
        ServiceLifecycleState.STOPPING,
        ServiceLifecycleEvent.STOP_REQUESTED,
    ): ServiceLifecycleState.STOPPING,
    (
        ServiceLifecycleState.STOPPING,
        ServiceLifecycleEvent.STOP_SUCCEEDED,
    ): ServiceLifecycleState.STOPPED,
    (
        ServiceLifecycleState.STOPPING,
        ServiceLifecycleEvent.STOP_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.STOPPING,
        ServiceLifecycleEvent.RUNTIME_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.STOPPING,
        ServiceLifecycleEvent.OWNERSHIP_LOST,
    ): ServiceLifecycleState.LOST,
    (
        ServiceLifecycleState.FAILED,
        ServiceLifecycleEvent.START_REQUESTED,
    ): ServiceLifecycleState.STARTING,
    (
        ServiceLifecycleState.FAILED,
        ServiceLifecycleEvent.RUNTIME_FAILED,
    ): ServiceLifecycleState.FAILED,
    (
        ServiceLifecycleState.LOST,
        ServiceLifecycleEvent.OWNERSHIP_LOST,
    ): ServiceLifecycleState.LOST,
    (
        ServiceLifecycleState.LOST,
        ServiceLifecycleEvent.RECONCILE_STOPPED,
    ): ServiceLifecycleState.STOPPED,
}

OPERATION_TRANSITIONS = {
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.CLAIM,
    ): OperationLifecycleState.CLAIMED,
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.FAIL,
    ): OperationLifecycleState.FAILED,
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.RETRY_READY,
    ): OperationLifecycleState.QUEUED,
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.CANCEL,
    ): OperationLifecycleState.CANCELLED,
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.CLAIM,
    ): OperationLifecycleState.CLAIMED,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.START,
    ): OperationLifecycleState.RUNNING,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.FAIL,
    ): OperationLifecycleState.FAILED,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.CANCEL,
    ): OperationLifecycleState.CANCELLED,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.START,
    ): OperationLifecycleState.RUNNING,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.SUCCEED,
    ): OperationLifecycleState.SUCCEEDED,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.FAIL,
    ): OperationLifecycleState.FAILED,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.CANCEL,
    ): OperationLifecycleState.CANCELLED,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleEvent.FAIL,
    ): OperationLifecycleState.FAILED,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleEvent.RETRY_SCHEDULED,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleEvent.RETRY_READY,
    ): OperationLifecycleState.QUEUED,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleEvent.CANCEL,
    ): OperationLifecycleState.CANCELLED,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.SUCCEEDED,
        OperationLifecycleEvent.SUCCEED,
    ): OperationLifecycleState.SUCCEEDED,
    (
        OperationLifecycleState.SUCCEEDED,
        OperationLifecycleEvent.RETRY_REQUESTED,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.FAILED,
        OperationLifecycleEvent.FAIL,
    ): OperationLifecycleState.FAILED,
    (
        OperationLifecycleState.FAILED,
        OperationLifecycleEvent.RETRY_REQUESTED,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.CANCELLED,
        OperationLifecycleEvent.CANCEL,
    ): OperationLifecycleState.CANCELLED,
    (
        OperationLifecycleState.LOST,
        OperationLifecycleEvent.OWNERSHIP_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.QUEUED,
        OperationLifecycleEvent.INTEGRITY_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.CLAIMED,
        OperationLifecycleEvent.INTEGRITY_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.RUNNING,
        OperationLifecycleEvent.INTEGRITY_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.RETRY_WAIT,
        OperationLifecycleEvent.INTEGRITY_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.SUCCEEDED,
        OperationLifecycleEvent.INTEGRITY_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.LOST,
        OperationLifecycleEvent.INTEGRITY_LOST,
    ): OperationLifecycleState.LOST,
    (
        OperationLifecycleState.LOST,
        OperationLifecycleEvent.RECONCILE_RETRY,
    ): OperationLifecycleState.RETRY_WAIT,
    (
        OperationLifecycleState.LOST,
        OperationLifecycleEvent.RECONCILE_FAIL,
    ): OperationLifecycleState.FAILED,
}


@pytest.mark.parametrize(
    ("current_state", "event", "expected_state"),
    [
        (
            ServiceLifecycleState.STOPPED,
            ServiceLifecycleEvent.START_REQUESTED,
            ServiceLifecycleState.STARTING,
        ),
        (
            ServiceLifecycleState.STARTING,
            ServiceLifecycleEvent.START_SUCCEEDED,
            ServiceLifecycleState.RUNNING,
        ),
        (
            ServiceLifecycleState.RUNNING,
            ServiceLifecycleEvent.HEALTH_DEGRADED,
            ServiceLifecycleState.DEGRADED,
        ),
        (
            ServiceLifecycleState.DEGRADED,
            ServiceLifecycleEvent.HEALTH_RESTORED,
            ServiceLifecycleState.RUNNING,
        ),
        (
            ServiceLifecycleState.RUNNING,
            ServiceLifecycleEvent.STOP_REQUESTED,
            ServiceLifecycleState.STOPPING,
        ),
        (
            ServiceLifecycleState.STOPPING,
            ServiceLifecycleEvent.STOP_SUCCEEDED,
            ServiceLifecycleState.STOPPED,
        ),
        (
            ServiceLifecycleState.RUNNING,
            ServiceLifecycleEvent.OWNERSHIP_LOST,
            ServiceLifecycleState.LOST,
        ),
        (
            ServiceLifecycleState.LOST,
            ServiceLifecycleEvent.RECONCILE_STOPPED,
            ServiceLifecycleState.STOPPED,
        ),
    ],
)
def test_service_lifecycle_transition_matrix(
    current_state: ServiceLifecycleState,
    event: ServiceLifecycleEvent,
    expected_state: ServiceLifecycleState,
) -> None:
    assert transition_service_lifecycle(current_state, event) is expected_state


@pytest.mark.parametrize(
    ("current_state", "event", "expected_state"),
    [
        (
            OperationLifecycleState.QUEUED,
            OperationLifecycleEvent.CLAIM,
            OperationLifecycleState.CLAIMED,
        ),
        (
            OperationLifecycleState.CLAIMED,
            OperationLifecycleEvent.START,
            OperationLifecycleState.RUNNING,
        ),
        (
            OperationLifecycleState.RUNNING,
            OperationLifecycleEvent.RETRY_SCHEDULED,
            OperationLifecycleState.RETRY_WAIT,
        ),
        (
            OperationLifecycleState.RETRY_WAIT,
            OperationLifecycleEvent.RETRY_READY,
            OperationLifecycleState.QUEUED,
        ),
        (
            OperationLifecycleState.FAILED,
            OperationLifecycleEvent.RETRY_REQUESTED,
            OperationLifecycleState.RETRY_WAIT,
        ),
        (
            OperationLifecycleState.RUNNING,
            OperationLifecycleEvent.SUCCEED,
            OperationLifecycleState.SUCCEEDED,
        ),
        (
            OperationLifecycleState.RUNNING,
            OperationLifecycleEvent.OWNERSHIP_LOST,
            OperationLifecycleState.LOST,
        ),
        (
            OperationLifecycleState.SUCCEEDED,
            OperationLifecycleEvent.INTEGRITY_LOST,
            OperationLifecycleState.LOST,
        ),
        (
            OperationLifecycleState.LOST,
            OperationLifecycleEvent.RECONCILE_RETRY,
            OperationLifecycleState.RETRY_WAIT,
        ),
    ],
)
def test_operation_lifecycle_transition_matrix(
    current_state: OperationLifecycleState,
    event: OperationLifecycleEvent,
    expected_state: OperationLifecycleState,
) -> None:
    assert transition_operation_lifecycle(current_state, event) is expected_state


@pytest.mark.parametrize(
    ("current_state", "event"),
    [
        (ServiceLifecycleState.STOPPED, ServiceLifecycleEvent.START_SUCCEEDED),
        (ServiceLifecycleState.LOST, ServiceLifecycleEvent.START_REQUESTED),
    ],
)
def test_service_lifecycle_rejects_impossible_transition(
    current_state: ServiceLifecycleState,
    event: ServiceLifecycleEvent,
) -> None:
    with pytest.raises(ValueError, match="invalid service lifecycle transition"):
        transition_service_lifecycle(current_state, event)


@pytest.mark.parametrize(
    ("current_state", "event"),
    [
        (OperationLifecycleState.SUCCEEDED, OperationLifecycleEvent.CLAIM),
        (OperationLifecycleState.FAILED, OperationLifecycleEvent.RETRY_READY),
        (OperationLifecycleState.CANCELLED, OperationLifecycleEvent.START),
    ],
)
def test_operation_lifecycle_keeps_terminal_states_closed(
    current_state: OperationLifecycleState,
    event: OperationLifecycleEvent,
) -> None:
    with pytest.raises(ValueError, match="invalid operation lifecycle transition"):
        transition_operation_lifecycle(current_state, event)


def test_lifecycle_transition_requires_native_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import endoreg_db.utils.rust_backend as rust_backend_module

    monkeypatch.setattr(rust_backend_module, "_transition_service_lifecycle", None)

    with pytest.raises(RuntimeError, match="required Rust lifecycle_state_machine"):
        rust_backend_module.transition_service_lifecycle(
            current_state="stopped",
            event="start_requested",
        )


def test_native_lifecycle_capability_is_versioned() -> None:
    assert has_native_capability(
        "lifecycle_state_machine",
        "lifecycle_state_v3",
    )


def test_service_lifecycle_matrix_is_exhaustive() -> None:
    for current_state in ServiceLifecycleState:
        for event in ServiceLifecycleEvent:
            expected_state = SERVICE_TRANSITIONS.get((current_state, event))
            if expected_state is None:
                with pytest.raises(
                    ValueError,
                    match="invalid service lifecycle transition",
                ):
                    transition_service_lifecycle(current_state, event)
            else:
                assert (
                    transition_service_lifecycle(current_state, event) is expected_state
                )


def test_operation_lifecycle_matrix_is_exhaustive() -> None:
    for current_state in OperationLifecycleState:
        for event in OperationLifecycleEvent:
            expected_state = OPERATION_TRANSITIONS.get((current_state, event))
            if expected_state is None:
                with pytest.raises(
                    ValueError,
                    match="invalid operation lifecycle transition",
                ):
                    transition_operation_lifecycle(current_state, event)
            else:
                assert (
                    transition_operation_lifecycle(current_state, event)
                    is expected_state
                )
