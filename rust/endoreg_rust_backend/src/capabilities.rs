use pyo3::prelude::*;

const BACKEND_IMPLEMENTATION_VERSION: &str = env!("CARGO_PKG_VERSION");

#[pyfunction]
pub(crate) fn native_capabilities() -> Vec<(String, String, String)> {
    vec![(
        "report_source_snapshot".to_owned(),
        "report_source_snapshot_v1".to_owned(),
        BACKEND_IMPLEMENTATION_VERSION.to_owned(),
    )]
}
