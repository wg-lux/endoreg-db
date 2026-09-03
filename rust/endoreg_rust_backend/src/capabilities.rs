use pyo3::prelude::*;

const BACKEND_IMPLEMENTATION_VERSION: &str = env!("CARGO_PKG_VERSION");

#[pyfunction]
pub(crate) fn native_capabilities() -> Vec<(String, String, String)> {
    vec![
        (
            "report_source_snapshot".to_owned(),
            "report_source_snapshot_v1".to_owned(),
            BACKEND_IMPLEMENTATION_VERSION.to_owned(),
        ),
        (
            "batch_file_identity".to_owned(),
            "batch_file_identity_v1".to_owned(),
            BACKEND_IMPLEMENTATION_VERSION.to_owned(),
        ),
        (
            "hls_state_machine".to_owned(),
            "hls_state_v1".to_owned(),
            BACKEND_IMPLEMENTATION_VERSION.to_owned(),
        ),
        (
            "lifecycle_state_machine".to_owned(),
            "lifecycle_state_v3".to_owned(),
            BACKEND_IMPLEMENTATION_VERSION.to_owned(),
        ),
    ]
}
