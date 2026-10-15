use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ServiceState {
    Stopped,
    Starting,
    Running,
    Degraded,
    Stopping,
    Failed,
    Lost,
}

impl ServiceState {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "stopped" => Ok(Self::Stopped),
            "starting" => Ok(Self::Starting),
            "running" => Ok(Self::Running),
            "degraded" => Ok(Self::Degraded),
            "stopping" => Ok(Self::Stopping),
            "failed" => Ok(Self::Failed),
            "lost" => Ok(Self::Lost),
            _ => Err(PyValueError::new_err(format!(
                "unsupported service lifecycle state: {value}"
            ))),
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Stopped => "stopped",
            Self::Starting => "starting",
            Self::Running => "running",
            Self::Degraded => "degraded",
            Self::Stopping => "stopping",
            Self::Failed => "failed",
            Self::Lost => "lost",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ServiceEvent {
    StartRequested,
    StartSucceeded,
    StartFailed,
    HealthDegraded,
    HealthRestored,
    StopRequested,
    StopSucceeded,
    StopFailed,
    RuntimeFailed,
    OwnershipLost,
    ReconcileStopped,
}

impl ServiceEvent {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "start_requested" => Ok(Self::StartRequested),
            "start_succeeded" => Ok(Self::StartSucceeded),
            "start_failed" => Ok(Self::StartFailed),
            "health_degraded" => Ok(Self::HealthDegraded),
            "health_restored" => Ok(Self::HealthRestored),
            "stop_requested" => Ok(Self::StopRequested),
            "stop_succeeded" => Ok(Self::StopSucceeded),
            "stop_failed" => Ok(Self::StopFailed),
            "runtime_failed" => Ok(Self::RuntimeFailed),
            "ownership_lost" => Ok(Self::OwnershipLost),
            "reconcile_stopped" => Ok(Self::ReconcileStopped),
            _ => Err(PyValueError::new_err(format!(
                "unsupported service lifecycle event: {value}"
            ))),
        }
    }
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

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OperationState {
    Queued,
    Claimed,
    Running,
    RetryWait,
    Succeeded,
    Failed,
    Cancelled,
    Lost,
}

impl OperationState {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "queued" => Ok(Self::Queued),
            "claimed" => Ok(Self::Claimed),
            "running" => Ok(Self::Running),
            "retry_wait" => Ok(Self::RetryWait),
            "succeeded" => Ok(Self::Succeeded),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            "lost" => Ok(Self::Lost),
            _ => Err(PyValueError::new_err(format!(
                "unsupported operation lifecycle state: {value}"
            ))),
        }
    }

    const fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Claimed => "claimed",
            Self::Running => "running",
            Self::RetryWait => "retry_wait",
            Self::Succeeded => "succeeded",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::Lost => "lost",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OperationEvent {
    Claim,
    Start,
    Succeed,
    Fail,
    RetryScheduled,
    RetryReady,
    RetryRequested,
    Cancel,
    OwnershipLost,
    IntegrityLost,
    ReconcileRetry,
    ReconcileFail,
}

impl OperationEvent {
    fn parse(value: &str) -> PyResult<Self> {
        match value {
            "claim" => Ok(Self::Claim),
            "start" => Ok(Self::Start),
            "succeed" => Ok(Self::Succeed),
            "fail" => Ok(Self::Fail),
            "retry_scheduled" => Ok(Self::RetryScheduled),
            "retry_ready" => Ok(Self::RetryReady),
            "retry_requested" => Ok(Self::RetryRequested),
            "cancel" => Ok(Self::Cancel),
            "ownership_lost" => Ok(Self::OwnershipLost),
            "integrity_lost" => Ok(Self::IntegrityLost),
            "reconcile_retry" => Ok(Self::ReconcileRetry),
            "reconcile_fail" => Ok(Self::ReconcileFail),
            _ => Err(PyValueError::new_err(format!(
                "unsupported operation lifecycle event: {value}"
            ))),
        }
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
    let current = ServiceState::parse(current_state)?;
    let parsed_event = ServiceEvent::parse(event)?;
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
    let current = OperationState::parse(current_state)?;
    let parsed_event = OperationEvent::parse(event)?;
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
