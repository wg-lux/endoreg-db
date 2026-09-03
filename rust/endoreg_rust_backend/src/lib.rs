mod capabilities;
mod encrypted_range;
mod encryption_state;
mod errors;
mod file_copy;
mod file_identity;
mod frames;
mod hashing;
mod hls_state;
mod import_state;
mod lifecycle_state;
mod pdf;
mod state_enums;
mod storage_profile;
mod stubs;

use pyo3::prelude::*;

pub use stubs::stub_info;

#[pymodule]
fn endoreg_rust_backend(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<file_identity::BatchProcessor>()?;
    module.add_function(wrap_pyfunction!(capabilities::native_capabilities, module)?)?;
    module.add_function(wrap_pyfunction!(
        encryption_state::encryption_status,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        encryption_state::is_lx_encrypted_file,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        encrypted_range::decrypt_encrypted_file_range,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        file_copy::copy_file_descriptor_to_path,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        file_identity::stable_file_identity,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        file_identity::stable_snapshot_to_path,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(hashing::sha256_file_hex, module)?)?;
    module.add_function(wrap_pyfunction!(
        hls_state::derive_hls_reservation_action,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        hls_state::derive_hls_publication_action,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        hls_state::derive_hls_reconciliation_action,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(pdf::render_single_page_pdf, module)?)?;
    module.add_function(wrap_pyfunction!(
        frames::parse_extracted_frame_numbers,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(frames::build_frame_records, module)?)?;
    module.add_function(wrap_pyfunction!(
        frames::build_expected_frame_records,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        import_state::derive_anonymization_status,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        import_state::derive_report_anonymization_status,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        lifecycle_state::transition_service_lifecycle,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        lifecycle_state::transition_operation_lifecycle,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        state_enums::derive_segment_annotation_status,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        state_enums::derive_frame_annotation_status,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        state_enums::normalize_frame_task_mode_token,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        state_enums::normalize_frame_sampling_strategy_token,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        storage_profile::storage_profile_policy_rows,
        module
    )?)?;
    Ok(())
}
