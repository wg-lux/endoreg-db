use crate::state_token::{state_tokens, Vocabulary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

state_tokens! {
    /// Persisted artifact statuses that still hold one materialization attempt.
    enum InFlightStatus {
        Queued => "queued",
        Materializing => "materializing",
        Validated => "validated",
    }
}

state_tokens! {
    enum ReservationAction {
        AlreadyReady => "already_ready",
        AlreadyInFlight => "already_in_flight",
        Queue => "queue",
    }
}

state_tokens! {
    enum PublicationAction {
        Reject => "reject",
        Defer => "defer",
        ReplaceReady => "replace_ready",
        PublishInitial => "publish_initial",
    }
}

state_tokens! {
    enum ReconciliationAction {
        FailAndCleanup => "fail_and_cleanup",
        Preserve => "preserve",
    }
}

state_tokens! {
    /// Every persisted ``VideoHlsArtifact.status``.
    enum ArtifactStatus {
        Queued => "queued",
        Materializing => "materializing",
        Validated => "validated",
        Ready => "ready",
        Superseded => "superseded",
        Failed => "failed",
    }
}

pub(crate) fn vocabularies() -> Vec<Vocabulary> {
    vec![
        ("hls_artifact_status", ArtifactStatus::tokens()),
        ("hls_in_flight_status", InFlightStatus::tokens()),
        ("hls_reservation_action", ReservationAction::tokens()),
        ("hls_publication_action", PublicationAction::tokens()),
        ("hls_reconciliation_action", ReconciliationAction::tokens()),
    ]
}

/// An empty token means no in-flight attempt; any other token must be in flight.
fn parse_optional_in_flight_status(status: &str) -> PyResult<Option<InFlightStatus>> {
    if status.is_empty() {
        return Ok(None);
    }
    InFlightStatus::parse(status)
        .map(Some)
        .ok_or_else(|| PyValueError::new_err(format!("unsupported HLS in-flight status: {status}")))
}

fn reservation_action(
    active_status: Option<InFlightStatus>,
    active_is_stale: bool,
    ready_matches_source: bool,
    force: bool,
) -> ReservationAction {
    match active_status {
        None if ready_matches_source && !force => ReservationAction::AlreadyReady,
        Some(_) if !active_is_stale => ReservationAction::AlreadyInFlight,
        _ => ReservationAction::Queue,
    }
}

fn publication_action(
    attempt_status: Option<InFlightStatus>,
    owner_matches: bool,
    has_active_lease: bool,
    has_ready_generation: bool,
) -> PublicationAction {
    let can_publish = attempt_status == Some(InFlightStatus::Validated) && owner_matches;
    match (can_publish, has_active_lease) {
        (false, _) => PublicationAction::Reject,
        (true, true) => PublicationAction::Defer,
        (true, false) if has_ready_generation => PublicationAction::ReplaceReady,
        (true, false) => PublicationAction::PublishInitial,
    }
}

fn reconciliation_action(status: Option<InFlightStatus>, is_stale: bool) -> ReconciliationAction {
    if status.is_some() && is_stale {
        return ReconciliationAction::FailAndCleanup;
    }
    ReconciliationAction::Preserve
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ArtifactTransition {
    /// Persist the target status.
    Apply,
    /// The artifact already reached or passed the target; write nothing.
    Ignore,
}

/// Status transition table for one HLS artifact row; ``None`` is an unsaved row.
///
/// ``Err`` marks a transition that would skip validation or revive a final
/// artifact, which indicates an ownership or integrity bug in the caller.
fn artifact_transition(
    current: Option<ArtifactStatus>,
    target: ArtifactStatus,
) -> Result<ArtifactTransition, ()> {
    use ArtifactStatus::*;
    use ArtifactTransition::*;
    match (current, target) {
        // A reservation or a claim creates the row.
        (None, Queued | Materializing) => Ok(Apply),
        (None, _) => Err(()),
        (Some(Queued), Materializing | Failed) => Ok(Apply),
        (Some(Queued), Queued) => Ok(Ignore),
        // The same fenced attempt may refresh its claim after a stale heartbeat.
        (Some(Materializing), Materializing | Validated | Failed) => Ok(Apply),
        (Some(Validated), Ready | Failed) => Ok(Apply),
        (Some(Validated), Validated) => Ok(Ignore),
        (Some(Ready), Superseded | Failed) => Ok(Apply),
        (Some(Ready), Ready) => Ok(Ignore),
        // Final statuses: late failure reports are stale, everything else is a bug.
        (Some(Superseded), Superseded | Failed) => Ok(Ignore),
        (Some(Failed), Failed) => Ok(Ignore),
        (Some(Queued | Materializing | Validated | Ready | Superseded | Failed), _) => Err(()),
    }
}

fn parse_artifact_status(status: &str) -> PyResult<ArtifactStatus> {
    ArtifactStatus::parse(status)
        .ok_or_else(|| PyValueError::new_err(format!("unsupported HLS artifact status: {status}")))
}

/// True applies the target status, False ignores it; illegal transitions raise ValueError.
#[pyfunction]
pub(crate) fn decide_hls_artifact_transition(
    current_status: &str,
    target_status: &str,
) -> PyResult<bool> {
    let current = if current_status.is_empty() {
        None
    } else {
        Some(parse_artifact_status(current_status)?)
    };
    let target = parse_artifact_status(target_status)?;
    match artifact_transition(current, target) {
        Ok(decision) => Ok(decision == ArtifactTransition::Apply),
        Err(()) => Err(PyValueError::new_err(format!(
            "illegal HLS artifact transition: {} -> {target_status}",
            if current_status.is_empty() { "<new>" } else { current_status }
        ))),
    }
}

#[pyfunction]
pub(crate) fn derive_hls_reservation_action(
    active_status: &str,
    active_is_stale: bool,
    ready_matches_source: bool,
    force: bool,
) -> PyResult<&'static str> {
    let active = parse_optional_in_flight_status(active_status)?;
    Ok(reservation_action(active, active_is_stale, ready_matches_source, force).as_str())
}

#[pyfunction]
pub(crate) fn derive_hls_publication_action(
    attempt_status: &str,
    owner_matches: bool,
    has_active_lease: bool,
    has_ready_generation: bool,
) -> PyResult<&'static str> {
    let attempt = parse_optional_in_flight_status(attempt_status)?;
    Ok(publication_action(
        attempt,
        owner_matches,
        has_active_lease,
        has_ready_generation,
    )
    .as_str())
}

#[pyfunction]
pub(crate) fn derive_hls_reconciliation_action(
    status: &str,
    is_stale: bool,
) -> PyResult<&'static str> {
    let parsed = parse_optional_in_flight_status(status)?;
    Ok(reconciliation_action(parsed, is_stale).as_str())
}

#[cfg(test)]
mod tests {
    use super::{
        artifact_transition, decide_hls_artifact_transition, derive_hls_publication_action,
        derive_hls_reconciliation_action, derive_hls_reservation_action, ArtifactStatus,
        ArtifactTransition,
    };

    /// The full table: every (current, target) pair that is not an error.
    fn expected(current: Option<ArtifactStatus>, target: ArtifactStatus) -> Option<ArtifactTransition> {
        use ArtifactStatus::*;
        use ArtifactTransition::*;
        match (current, target) {
            (None, Queued | Materializing)
            | (Some(Queued), Materializing | Failed)
            | (Some(Materializing), Materializing | Validated | Failed)
            | (Some(Validated), Ready | Failed)
            | (Some(Ready), Superseded | Failed) => Some(Apply),
            (Some(Queued), Queued)
            | (Some(Validated), Validated)
            | (Some(Ready), Ready)
            | (Some(Superseded), Superseded | Failed)
            | (Some(Failed), Failed) => Some(Ignore),
            _ => None,
        }
    }

    fn all_currents() -> Vec<Option<ArtifactStatus>> {
        std::iter::once(None)
            .chain(ArtifactStatus::ALL.iter().copied().map(Some))
            .collect()
    }

    #[test]
    fn artifact_transition_table_is_exhaustive() {
        for current in all_currents() {
            for &target in ArtifactStatus::ALL {
                assert_eq!(
                    artifact_transition(current, target).ok(),
                    expected(current, target),
                    "{current:?} -> {target:?}"
                );
            }
        }
    }

    #[test]
    fn final_artifacts_are_never_revived_and_ready_requires_validation() {
        use ArtifactStatus::*;
        for current in [Some(Superseded), Some(Failed)] {
            for &target in ArtifactStatus::ALL {
                assert_ne!(artifact_transition(current, target), Ok(ArtifactTransition::Apply));
            }
        }
        for current in all_currents() {
            if current != Some(Validated) && current != Some(Ready) {
                assert!(artifact_transition(current, Ready).is_err(), "{current:?} -> ready");
            }
        }
    }

    #[test]
    fn applied_targets_are_idempotent_or_explicit_reclaims() {
        for current in all_currents() {
            for &target in ArtifactStatus::ALL {
                if artifact_transition(current, target) == Ok(ArtifactTransition::Apply) {
                    let again = artifact_transition(Some(target), target).unwrap();
                    let reclaim = target == ArtifactStatus::Materializing;
                    assert_eq!(again == ArtifactTransition::Apply, reclaim, "{target:?}");
                }
            }
        }
    }

    #[test]
    fn artifact_transition_entry_point_fails_closed() {
        assert!(decide_hls_artifact_transition("", "queued").unwrap());
        assert!(!decide_hls_artifact_transition("failed", "failed").unwrap());
        assert!(decide_hls_artifact_transition("queued", "ready").is_err());
        assert!(decide_hls_artifact_transition("lost", "failed").is_err());
        assert!(decide_hls_artifact_transition("queued", "").is_err());
    }

    #[test]
    fn reservation_preserves_one_live_attempt_and_ready_generation() {
        assert_eq!(
            derive_hls_reservation_action("materializing", false, true, true).unwrap(),
            "already_in_flight"
        );
        assert_eq!(
            derive_hls_reservation_action("validated", true, true, true).unwrap(),
            "queue"
        );
        assert_eq!(
            derive_hls_reservation_action("", false, true, false).unwrap(),
            "already_ready"
        );
        assert_eq!(
            derive_hls_reservation_action("", false, true, true).unwrap(),
            "queue"
        );
    }

    #[test]
    fn publication_defers_for_leases_and_rejects_stale_owners() {
        assert_eq!(
            derive_hls_publication_action("validated", true, true, true).unwrap(),
            "defer"
        );
        assert_eq!(
            derive_hls_publication_action("validated", true, false, true).unwrap(),
            "replace_ready"
        );
        assert_eq!(
            derive_hls_publication_action("validated", true, false, false).unwrap(),
            "publish_initial"
        );
        assert_eq!(
            derive_hls_publication_action("validated", false, false, true).unwrap(),
            "reject"
        );
    }

    #[test]
    fn reconciliation_only_cleans_stale_in_flight_attempts() {
        assert_eq!(
            derive_hls_reconciliation_action("queued", true).unwrap(),
            "fail_and_cleanup"
        );
        assert_eq!(
            derive_hls_reconciliation_action("materializing", false).unwrap(),
            "preserve"
        );
        assert!(derive_hls_reconciliation_action("ready", true).is_err());
    }

    #[test]
    fn stale_attempts_are_replaced_even_when_a_ready_derivative_exists() {
        for status in ["queued", "materializing", "validated"] {
            assert_eq!(
                derive_hls_reservation_action(status, true, true, false).unwrap(),
                "queue"
            );
            assert_eq!(
                derive_hls_reservation_action(status, false, false, true).unwrap(),
                "already_in_flight"
            );
        }
        for status in ["", "queued", "materializing"] {
            assert_eq!(
                derive_hls_publication_action(status, true, true, true).unwrap(),
                "reject"
            );
        }
        assert!(derive_hls_reservation_action("ready", false, true, false).is_err());
        assert!(derive_hls_publication_action("failed", true, false, false).is_err());
    }
}
