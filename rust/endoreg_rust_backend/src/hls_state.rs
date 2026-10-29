use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

const QUEUED: &str = "queued";
const MATERIALIZING: &str = "materializing";
const VALIDATED: &str = "validated";

fn is_in_flight(status: &str) -> bool {
    matches!(status, QUEUED | MATERIALIZING | VALIDATED)
}

fn validate_optional_in_flight_status(status: &str) -> PyResult<()> {
    if status.is_empty() || is_in_flight(status) {
        return Ok(());
    }
    Err(PyValueError::new_err(format!(
        "unsupported HLS in-flight status: {status}"
    )))
}

#[pyfunction]
pub(crate) fn derive_hls_reservation_action(
    active_status: &str,
    active_is_stale: bool,
    ready_matches_source: bool,
    force: bool,
) -> PyResult<&'static str> {
    validate_optional_in_flight_status(active_status)?;
    Ok(match active_status {
        "" if ready_matches_source && !force => "already_ready",
        _ if !active_status.is_empty() && !active_is_stale => "already_in_flight",
        _ => "queue",
    })
}

#[pyfunction]
pub(crate) fn derive_hls_publication_action(
    attempt_status: &str,
    owner_matches: bool,
    has_active_lease: bool,
    has_ready_generation: bool,
) -> PyResult<&'static str> {
    validate_optional_in_flight_status(attempt_status)?;
    let can_publish = attempt_status == VALIDATED && owner_matches;
    Ok(match (can_publish, has_active_lease) {
        (false, _) => "reject",
        (true, true) => "defer",
        (true, false) if has_ready_generation => "replace_ready",
        _ => "publish_initial",
    })
}

#[pyfunction]
pub(crate) fn derive_hls_reconciliation_action(
    status: &str,
    is_stale: bool,
) -> PyResult<&'static str> {
    validate_optional_in_flight_status(status)?;
    if is_in_flight(status) && is_stale {
        return Ok("fail_and_cleanup");
    }
    Ok("preserve")
}

#[cfg(test)]
mod tests {
    use super::{
        derive_hls_publication_action, derive_hls_reconciliation_action,
        derive_hls_reservation_action,
    };

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
