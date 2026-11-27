use crate::state_token::{state_tokens, Vocabulary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

state_tokens! {
    enum ServiceState {
        Stopped => "stopped",
        Starting => "starting",
        Running => "running",
        Degraded => "degraded",
        Stopping => "stopping",
        Failed => "failed",
        Lost => "lost",
    }
}

state_tokens! {
    enum ServiceEvent {
        StartRequested => "start_requested",
        StartSucceeded => "start_succeeded",
        StartFailed => "start_failed",
        HealthDegraded => "health_degraded",
        HealthRestored => "health_restored",
        StopRequested => "stop_requested",
        StopSucceeded => "stop_succeeded",
        StopFailed => "stop_failed",
        RuntimeFailed => "runtime_failed",
        OwnershipLost => "ownership_lost",
        ReconcileStopped => "reconcile_stopped",
    }
}

state_tokens! {
    enum OperationState {
        Queued => "queued",
        Claimed => "claimed",
        Running => "running",
        RetryWait => "retry_wait",
        Succeeded => "succeeded",
        Failed => "failed",
        Cancelled => "cancelled",
        Lost => "lost",
    }
}

state_tokens! {
    enum OperationEvent {
        Claim => "claim",
        Start => "start",
        Succeed => "succeed",
        Fail => "fail",
        RetryScheduled => "retry_scheduled",
        RetryReady => "retry_ready",
        RetryRequested => "retry_requested",
        Cancel => "cancel",
        OwnershipLost => "ownership_lost",
        IntegrityLost => "integrity_lost",
        ReconcileRetry => "reconcile_retry",
        ReconcileFail => "reconcile_fail",
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        ("service_lifecycle_state", ServiceState::tokens()),
        ("service_lifecycle_event", ServiceEvent::tokens()),
        ("operation_lifecycle_state", OperationState::tokens()),
        ("operation_lifecycle_event", OperationEvent::tokens()),
    ]
}

fn parse_token<T>(value: &str, parsed: Option<T>, kind: &str) -> PyResult<T> {
    parsed.ok_or_else(|| PyValueError::new_err(format!("unsupported {kind}: {value}")))
}

fn reduce_service_state(current: ServiceState, event: ServiceEvent) -> Option<ServiceState> {
    use ServiceEvent as Event;
    use ServiceState as State;

    match (current, event) {
        (State::Stopped | State::Failed, Event::StartRequested) => Some(State::Starting),
        (State::Starting, Event::StartRequested) => Some(State::Starting),
        (State::Starting, Event::StartSucceeded) => Some(State::Running),
        (State::Running, Event::StartSucceeded) => Some(State::Running),
        (State::Starting, Event::StartFailed) => Some(State::Failed),
        (State::Running, Event::HealthDegraded) => Some(State::Degraded),
        (State::Degraded, Event::HealthDegraded) => Some(State::Degraded),
        (State::Degraded, Event::HealthRestored) => Some(State::Running),
        (State::Running, Event::HealthRestored) => Some(State::Running),
        (State::Starting | State::Running | State::Degraded, Event::StopRequested) => {
            Some(State::Stopping)
        }
        (State::Stopping, Event::StopRequested) => Some(State::Stopping),
        (State::Stopping, Event::StopSucceeded) => Some(State::Stopped),
        (State::Stopped, Event::StopSucceeded) => Some(State::Stopped),
        (State::Stopping, Event::StopFailed) => Some(State::Failed),
        (
            State::Starting | State::Running | State::Degraded | State::Stopping,
            Event::RuntimeFailed,
        ) => Some(State::Failed),
        (State::Failed, Event::RuntimeFailed) => Some(State::Failed),
        (
            State::Starting | State::Running | State::Degraded | State::Stopping,
            Event::OwnershipLost,
        ) => Some(State::Lost),
        (State::Lost, Event::OwnershipLost) => Some(State::Lost),
        (State::Lost, Event::ReconcileStopped) => Some(State::Stopped),
        (State::Stopped, Event::ReconcileStopped) => Some(State::Stopped),
        _ => None,
    }
}

fn reduce_operation_state(
    current: OperationState,
    event: OperationEvent,
) -> Option<OperationState> {
    use OperationEvent as Event;
    use OperationState as State;

    match (current, event) {
        (State::Queued, Event::Claim) => Some(State::Claimed),
        (State::Claimed, Event::Claim) => Some(State::Claimed),
        (State::Claimed, Event::Start) => Some(State::Running),
        (State::Running, Event::Start) => Some(State::Running),
        (State::Running, Event::Succeed) => Some(State::Succeeded),
        (State::Succeeded, Event::Succeed) => Some(State::Succeeded),
        (State::Queued | State::Claimed | State::Running | State::RetryWait, Event::Fail) => {
            Some(State::Failed)
        }
        (State::Failed, Event::Fail) => Some(State::Failed),
        (State::Queued | State::Claimed | State::Running, Event::RetryScheduled) => {
            Some(State::RetryWait)
        }
        (State::RetryWait, Event::RetryScheduled) => Some(State::RetryWait),
        (State::RetryWait, Event::RetryReady) => Some(State::Queued),
        (State::Queued, Event::RetryReady) => Some(State::Queued),
        (State::Failed | State::Succeeded, Event::RetryRequested) => Some(State::RetryWait),
        (State::Queued | State::Claimed | State::Running | State::RetryWait, Event::Cancel) => {
            Some(State::Cancelled)
        }
        (State::Cancelled, Event::Cancel) => Some(State::Cancelled),
        (
            State::Queued | State::Claimed | State::Running | State::RetryWait,
            Event::OwnershipLost,
        ) => Some(State::Lost),
        (State::Lost, Event::OwnershipLost) => Some(State::Lost),
        (
            State::Queued | State::Claimed | State::Running | State::RetryWait | State::Succeeded,
            Event::IntegrityLost,
        ) => Some(State::Lost),
        (State::Lost, Event::IntegrityLost) => Some(State::Lost),
        (State::Lost, Event::ReconcileRetry) => Some(State::RetryWait),
        (State::Lost, Event::ReconcileFail) => Some(State::Failed),
        _ => None,
    }
}

#[pyfunction]
pub(crate) fn transition_service_lifecycle(
    current_state: &str,
    event: &str,
) -> PyResult<&'static str> {
    let current = parse_token(
        current_state,
        ServiceState::parse(current_state),
        "service lifecycle state",
    )?;
    let parsed_event = parse_token(event, ServiceEvent::parse(event), "service lifecycle event")?;
    reduce_service_state(current, parsed_event)
        .map(ServiceState::as_str)
        .ok_or_else(|| {
            PyValueError::new_err(format!(
                "invalid service lifecycle transition: {current_state} --{event}-->"
            ))
        })
}

#[pyfunction]
pub(crate) fn transition_operation_lifecycle(
    current_state: &str,
    event: &str,
) -> PyResult<&'static str> {
    let current = parse_token(
        current_state,
        OperationState::parse(current_state),
        "operation lifecycle state",
    )?;
    let parsed_event = parse_token(
        event,
        OperationEvent::parse(event),
        "operation lifecycle event",
    )?;
    reduce_operation_state(current, parsed_event)
        .map(OperationState::as_str)
        .ok_or_else(|| {
            PyValueError::new_err(format!(
                "invalid operation lifecycle transition: {current_state} --{event}-->"
            ))
        })
}

#[cfg(test)]
mod tests {
    use super::{transition_operation_lifecycle, transition_service_lifecycle};

    #[test]
    fn service_lifecycle_supports_recovery_and_idempotent_redelivery() {
        assert_eq!(
            transition_service_lifecycle("stopped", "start_requested").unwrap(),
            "starting"
        );
        assert_eq!(
            transition_service_lifecycle("starting", "start_requested").unwrap(),
            "starting"
        );
        assert_eq!(
            transition_service_lifecycle("starting", "start_succeeded").unwrap(),
            "running"
        );
        assert_eq!(
            transition_service_lifecycle("running", "health_degraded").unwrap(),
            "degraded"
        );
        assert_eq!(
            transition_service_lifecycle("degraded", "health_restored").unwrap(),
            "running"
        );
        assert_eq!(
            transition_service_lifecycle("running", "ownership_lost").unwrap(),
            "lost"
        );
        assert_eq!(
            transition_service_lifecycle("lost", "reconcile_stopped").unwrap(),
            "stopped"
        );
        assert_eq!(
            transition_service_lifecycle("failed", "start_requested").unwrap(),
            "starting"
        );
    }

    #[test]
    fn service_lifecycle_rejects_unknown_and_impossible_transitions() {
        assert!(transition_service_lifecycle("healthy", "stop_requested").is_err());
        assert!(transition_service_lifecycle("stopped", "start_succeeded").is_err());
        assert!(transition_service_lifecycle("running", "invented").is_err());
    }

    #[test]
    fn operation_lifecycle_supports_retry_recovery_and_idempotent_redelivery() {
        assert_eq!(
            transition_operation_lifecycle("queued", "claim").unwrap(),
            "claimed"
        );
        assert_eq!(
            transition_operation_lifecycle("claimed", "claim").unwrap(),
            "claimed"
        );
        assert_eq!(
            transition_operation_lifecycle("claimed", "start").unwrap(),
            "running"
        );
        assert_eq!(
            transition_operation_lifecycle("running", "retry_scheduled").unwrap(),
            "retry_wait"
        );
        assert_eq!(
            transition_operation_lifecycle("retry_wait", "retry_ready").unwrap(),
            "queued"
        );
        assert_eq!(
            transition_operation_lifecycle("failed", "retry_requested").unwrap(),
            "retry_wait"
        );
        assert_eq!(
            transition_operation_lifecycle("running", "ownership_lost").unwrap(),
            "lost"
        );
        assert_eq!(
            transition_operation_lifecycle("queued", "ownership_lost").unwrap(),
            "lost"
        );
        assert_eq!(
            transition_operation_lifecycle("retry_wait", "ownership_lost").unwrap(),
            "lost"
        );
        assert_eq!(
            transition_operation_lifecycle("queued", "fail").unwrap(),
            "failed"
        );
        assert_eq!(
            transition_operation_lifecycle("succeeded", "integrity_lost").unwrap(),
            "lost"
        );
        assert_eq!(
            transition_operation_lifecycle("lost", "integrity_lost").unwrap(),
            "lost"
        );
        assert_eq!(
            transition_operation_lifecycle("lost", "reconcile_retry").unwrap(),
            "retry_wait"
        );
    }

    #[test]
    fn operation_lifecycle_keeps_terminal_states_closed() {
        assert!(transition_operation_lifecycle("succeeded", "claim").is_err());
        assert!(transition_operation_lifecycle("failed", "retry_ready").is_err());
        assert!(transition_operation_lifecycle("cancelled", "start").is_err());
        assert!(transition_operation_lifecycle("unknown", "claim").is_err());
        assert!(transition_operation_lifecycle("queued", "invented").is_err());
    }
}
