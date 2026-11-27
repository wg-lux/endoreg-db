//! Outbound hub transfer state machine: the only owner of hub status changes.
//!
//! Every local status change is one event decided by `decide`. Decisions are
//! pure; callers hold the database row lock and persist only applied results,
//! so a duplicate, late or stale event becomes an explicit no-op instead of a
//! lost update. Completion is absorbing, a transfer that was never dispatched
//! rejects receiver acknowledgements, and the first non-retryable failure is
//! never replaced by a later or retryable one.
use crate::state_token::{state_tokens, Vocabulary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

state_tokens! {
    enum LocalStatus {
        Marked => "marked",
        Queued => "queued",
        Registering => "registering",
        AwaitingMedia => "awaiting_media",
        Uploading => "uploading",
        Completed => "completed",
        Failed => "failed",
    }
}

state_tokens! {
    enum RemoteStatus {
        Pending => "pending",
        Claimed => "claimed",
        Running => "running",
        RetryWait => "retry_wait",
        AwaitingMedia => "awaiting_media",
        Applied => "applied",
        Failed => "failed",
        Inconsistent => "inconsistent",
        Lost => "lost",
    }
}

state_tokens! {
    enum FailureClass {
        ConfigurationRejection => "configuration_rejection",
        AuthorizationDenial => "authorization_denial",
        IntegrityInconsistency => "integrity_inconsistency",
        TransientRetry => "transient_retry",
    }
}

state_tokens! {
    enum TransferEvent {
        Queue => "queue",
        Retry => "retry",
        OperatorRetry => "operator_retry",
        Redispatch => "redispatch",
        StartRegistration => "start_registration",
        StartUpload => "start_upload",
        Ineligible => "ineligible",
        Acknowledge => "acknowledge",
        Fail => "fail",
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        ("hub_local_status", LocalStatus::tokens()),
        ("hub_remote_status", RemoteStatus::tokens()),
        ("hub_failure_class", FailureClass::tokens()),
        ("hub_transfer_event", TransferEvent::tokens()),
    ]
}

/// Persisted status pair; a failure category exists only on failed transfers.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Snapshot {
    status: LocalStatus,
    failure: Option<FailureClass>,
}

/// One validated event; acknowledgement and failure carry their payload.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Input {
    Queue,
    Retry,
    OperatorRetry,
    Redispatch,
    StartRegistration,
    StartUpload,
    Ineligible,
    Acknowledge(RemoteStatus),
    Fail(FailureClass),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Decision {
    Apply(Snapshot),
    /// Duplicate, late or superseded event: the caller must not write.
    Ignore,
}

const fn to(status: LocalStatus) -> Decision {
    Decision::Apply(Snapshot {
        status,
        failure: None,
    })
}

const fn fail(failure: FailureClass) -> Decision {
    Decision::Apply(Snapshot {
        status: LocalStatus::Failed,
        failure: Some(failure),
    })
}

const fn in_flight(status: LocalStatus) -> bool {
    matches!(
        status,
        LocalStatus::Registering | LocalStatus::AwaitingMedia | LocalStatus::Uploading
    )
}

fn decide(current: Snapshot, input: Input) -> Result<Decision, &'static str> {
    use LocalStatus as S;

    let status = current.status;
    if status == S::Completed {
        return Ok(Decision::Ignore);
    }
    let decision = match input {
        Input::Queue if status == S::Marked => to(S::Queued),
        Input::Retry => match current.failure {
            // Rows without a recorded category predate failure classification.
            Some(FailureClass::TransientRetry) | None if status == S::Failed => to(S::Queued),
            _ => Decision::Ignore,
        },
        Input::OperatorRetry if status == S::Failed => to(S::Queued),
        Input::Redispatch if status == S::Queued => to(S::Queued),
        Input::StartRegistration if status == S::Queued || in_flight(status) => to(S::Registering),
        Input::StartUpload if status == S::AwaitingMedia => to(S::Uploading),
        Input::Ineligible if status == S::Queued || in_flight(status) => {
            fail(FailureClass::ConfigurationRejection)
        }
        Input::Acknowledge(remote) => acknowledge(status, remote)?,
        Input::Fail(failure) => match (status, current.failure) {
            (S::Failed, Some(previous))
                if previous == failure || previous != FailureClass::TransientRetry =>
            {
                Decision::Ignore
            }
            _ => fail(failure),
        },
        Input::Queue
        | Input::OperatorRetry
        | Input::Redispatch
        | Input::StartRegistration
        | Input::StartUpload
        | Input::Ineligible => Decision::Ignore,
    };
    Ok(decision)
}

fn acknowledge(status: LocalStatus, remote: RemoteStatus) -> Result<Decision, &'static str> {
    use LocalStatus as S;
    use RemoteStatus as R;

    if status == S::Marked {
        return Err("Hub acknowledgement for a transfer that was never dispatched");
    }
    if !in_flight(status) {
        // A superseded attempt: only hub completion with a validated receipt is authoritative.
        return Ok(if remote == R::Applied {
            to(S::Completed)
        } else {
            Decision::Ignore
        });
    }
    Ok(match remote {
        R::Applied => to(S::Completed),
        R::AwaitingMedia => to(S::AwaitingMedia),
        R::Failed => fail(FailureClass::ConfigurationRejection),
        R::Inconsistent | R::Lost => fail(FailureClass::IntegrityInconsistency),
        R::Pending | R::Claimed | R::Running | R::RetryWait => to(status),
    })
}

fn parse<T>(value: &str, parsed: Option<T>, kind: &str) -> PyResult<T> {
    parsed.ok_or_else(|| PyValueError::new_err(format!("Unknown hub {kind}: {value:?}")))
}

fn parse_input(
    event: &str,
    remote_status: Option<&str>,
    failure_class: Option<&str>,
) -> PyResult<Input> {
    let event = parse(event, TransferEvent::parse(event), "transfer event")?;
    let input = match (event, remote_status, failure_class) {
        (TransferEvent::Acknowledge, Some(remote), None) => {
            Input::Acknowledge(parse(remote, RemoteStatus::parse(remote), "remote status")?)
        }
        (TransferEvent::Fail, None, Some(failure)) => Input::Fail(parse(
            failure,
            FailureClass::parse(failure),
            "failure class",
        )?),
        (TransferEvent::Acknowledge | TransferEvent::Fail, _, _)
        | (_, Some(_), _)
        | (_, _, Some(_)) => {
            return Err(PyValueError::new_err(
                "Acknowledge requires only remote_status and fail requires only failure_class",
            ))
        }
        (TransferEvent::Queue, None, None) => Input::Queue,
        (TransferEvent::Retry, None, None) => Input::Retry,
        (TransferEvent::OperatorRetry, None, None) => Input::OperatorRetry,
        (TransferEvent::Redispatch, None, None) => Input::Redispatch,
        (TransferEvent::StartRegistration, None, None) => Input::StartRegistration,
        (TransferEvent::StartUpload, None, None) => Input::StartUpload,
        (TransferEvent::Ineligible, None, None) => Input::Ineligible,
    };
    Ok(input)
}

/// Returns `(status, failure_class, apply)`; on `apply == False` callers must not write.
#[pyfunction]
#[pyo3(signature = (current_state, current_failure_class, event, remote_status=None, failure_class=None))]
pub(crate) fn reduce_hub_transfer_event(
    current_state: &str,
    current_failure_class: Option<&str>,
    event: &str,
    remote_status: Option<&str>,
    failure_class: Option<&str>,
) -> PyResult<(String, Option<String>, bool)> {
    let current = Snapshot {
        status: parse(
            current_state,
            LocalStatus::parse(current_state),
            "local status",
        )?,
        failure: current_failure_class
            .map(|value| parse(value, FailureClass::parse(value), "failure class"))
            .transpose()?,
    };
    let input = parse_input(event, remote_status, failure_class)?;
    let decision = decide(current, input).map_err(PyValueError::new_err)?;
    Ok(match decision {
        Decision::Apply(next) => (
            next.status.as_str().to_owned(),
            next.failure.map(|value| value.as_str().to_owned()),
            true,
        ),
        Decision::Ignore => (
            current.status.as_str().to_owned(),
            current.failure.map(|value| value.as_str().to_owned()),
            false,
        ),
    })
}

#[pyfunction]
pub(crate) fn hub_failure_is_retryable(failure_class: &str) -> PyResult<bool> {
    let failure = parse(
        failure_class,
        FailureClass::parse(failure_class),
        "failure class",
    )?;
    Ok(failure == FailureClass::TransientRetry)
}

#[cfg(test)]
mod tests {
    use super::*;
    use LocalStatus as S;

    fn snapshots() -> Vec<Snapshot> {
        let mut all = Vec::new();
        for status in S::ALL {
            all.push(Snapshot {
                status: *status,
                failure: None,
            });
            if *status == S::Failed {
                for failure in FailureClass::ALL {
                    all.push(Snapshot {
                        status: *status,
                        failure: Some(*failure),
                    });
                }
            }
        }
        all
    }

    fn inputs() -> Vec<Input> {
        let mut all = vec![
            Input::Queue,
            Input::Retry,
            Input::OperatorRetry,
            Input::Redispatch,
            Input::StartRegistration,
            Input::StartUpload,
            Input::Ineligible,
        ];
        all.extend(
            RemoteStatus::ALL
                .iter()
                .map(|remote| Input::Acknowledge(*remote)),
        );
        all.extend(
            FailureClass::ALL
                .iter()
                .map(|failure| Input::Fail(*failure)),
        );
        all
    }

    #[test]
    fn every_applied_result_keeps_failure_exactly_on_failed_status() {
        for current in snapshots() {
            for input in inputs() {
                if let Ok(Decision::Apply(next)) = decide(current, input) {
                    assert_eq!(
                        next.status == S::Failed,
                        next.failure.is_some(),
                        "{current:?} {input:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn every_decision_is_idempotent_under_duplicate_delivery() {
        // Re-delivering the same event to its own result never changes it again.
        for current in snapshots() {
            for input in inputs() {
                if let Ok(Decision::Apply(next)) = decide(current, input) {
                    match decide(next, input) {
                        Ok(Decision::Apply(again)) => {
                            assert_eq!(again, next, "{current:?} {input:?}")
                        }
                        Ok(Decision::Ignore) => {}
                        Err(error) => panic!("{current:?} {input:?}: {error}"),
                    }
                }
            }
        }
    }

    #[test]
    fn completion_is_absorbing_for_every_event() {
        for input in inputs() {
            let completed = Snapshot {
                status: S::Completed,
                failure: None,
            };
            assert_eq!(decide(completed, input), Ok(Decision::Ignore), "{input:?}");
        }
    }

    #[test]
    fn non_retryable_failures_are_never_replaced_or_automatically_retried() {
        for previous in FailureClass::ALL
            .iter()
            .filter(|f| **f != FailureClass::TransientRetry)
        {
            let failed = Snapshot {
                status: S::Failed,
                failure: Some(*previous),
            };
            for failure in FailureClass::ALL {
                assert_eq!(decide(failed, Input::Fail(*failure)), Ok(Decision::Ignore));
            }
            for input in [Input::Retry, Input::StartRegistration, Input::Ineligible] {
                assert_eq!(decide(failed, input), Ok(Decision::Ignore), "{input:?}");
            }
            assert_eq!(decide(failed, Input::OperatorRetry), Ok(to(S::Queued)));
        }
        let transient = Snapshot {
            status: S::Failed,
            failure: Some(FailureClass::TransientRetry),
        };
        assert_eq!(decide(transient, Input::Retry), Ok(to(S::Queued)));
        assert_eq!(
            decide(transient, Input::Fail(FailureClass::IntegrityInconsistency)),
            Ok(fail(FailureClass::IntegrityInconsistency))
        );
    }

    #[test]
    fn acknowledgements_follow_the_dispatch_lifecycle() {
        for remote in RemoteStatus::ALL {
            let marked = Snapshot {
                status: S::Marked,
                failure: None,
            };
            assert!(decide(marked, Input::Acknowledge(*remote)).is_err());
            for status in [S::Queued, S::Failed] {
                let expected = if *remote == RemoteStatus::Applied {
                    to(S::Completed)
                } else {
                    Decision::Ignore
                };
                let superseded = Snapshot {
                    status,
                    failure: None,
                };
                assert_eq!(
                    decide(superseded, Input::Acknowledge(*remote)),
                    Ok(expected)
                );
            }
        }
        let uploading = Snapshot {
            status: S::Uploading,
            failure: None,
        };
        assert_eq!(
            decide(uploading, Input::Acknowledge(RemoteStatus::Lost)),
            Ok(fail(FailureClass::IntegrityInconsistency))
        );
        assert_eq!(
            decide(uploading, Input::Acknowledge(RemoteStatus::Running)),
            Ok(to(S::Uploading))
        );
    }

    #[test]
    fn worker_progress_stops_once_another_writer_owns_the_transfer() {
        for status in [S::Marked, S::Failed] {
            let current = Snapshot {
                status,
                failure: None,
            };
            assert_eq!(
                decide(current, Input::StartRegistration),
                Ok(Decision::Ignore)
            );
        }
        for status in S::ALL.iter().filter(|status| **status != S::AwaitingMedia) {
            let current = Snapshot {
                status: *status,
                failure: None,
            };
            assert_eq!(decide(current, Input::StartUpload), Ok(Decision::Ignore));
        }
        let queued = Snapshot {
            status: S::Queued,
            failure: None,
        };
        assert_eq!(
            decide(queued, Input::Ineligible),
            Ok(fail(FailureClass::ConfigurationRejection))
        );
    }

    #[test]
    fn unknown_and_ambiguous_input_is_rejected() {
        for args in [
            ("unknown", None, "queue", None, None),
            ("queued", Some("unknown"), "queue", None, None),
            ("queued", None, "unknown", None, None),
            ("completed", None, "acknowledge", Some("unknown"), None),
            ("completed", None, "fail", None, Some("unknown")),
            ("queued", None, "acknowledge", None, None),
            ("queued", None, "fail", None, None),
            (
                "queued",
                None,
                "acknowledge",
                Some("applied"),
                Some("transient_retry"),
            ),
            ("queued", None, "queue", Some("applied"), None),
            ("queued", None, "redispatch", None, Some("transient_retry")),
            ("marked", None, "acknowledge", Some("applied"), None),
        ] {
            assert!(
                reduce_hub_transfer_event(args.0, args.1, args.2, args.3, args.4).is_err(),
                "{args:?}"
            );
        }
        for failure in FailureClass::ALL {
            assert_eq!(
                hub_failure_is_retryable(failure.as_str()).unwrap(),
                *failure == FailureClass::TransientRetry
            );
        }
    }
}
