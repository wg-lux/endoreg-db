use pyo3_stub_gen::{
    define_stub_info_gatherer, inventory,
    type_info::{ArgInfo, PyFunctionInfo},
    TypeInfo,
};

fn stub_type_bytes() -> TypeInfo {
    TypeInfo::builtin("bytes")
}

fn stub_type_int() -> TypeInfo {
    TypeInfo::builtin("int")
}

fn stub_type_list_str() -> TypeInfo {
    TypeInfo::builtin("list[str]")
}

fn stub_type_list_int() -> TypeInfo {
    TypeInfo::builtin("list[int]")
}

fn stub_type_frame_records() -> TypeInfo {
    TypeInfo::builtin("list[tuple[int, str]]")
}

fn stub_type_storage_policy_rows() -> TypeInfo {
    TypeInfo::builtin("list[tuple[str, str, str]]")
}

fn stub_type_native_capabilities() -> TypeInfo {
    TypeInfo::builtin("list[tuple[str, str, str]]")
}

fn stub_type_file_identity() -> TypeInfo {
    TypeInfo::builtin("tuple[int, int, str]")
}

fn stub_type_path() -> TypeInfo {
    TypeInfo::with_module("pathlib.Path", "pathlib".into())
}

fn stub_type_str() -> TypeInfo {
    TypeInfo::builtin("str")
}

fn stub_type_bool() -> TypeInfo {
    TypeInfo::builtin("bool")
}

inventory::submit! {
    PyFunctionInfo {
        name: "native_capabilities",
        args: &[],
        r#return: stub_type_native_capabilities,
        doc: "",
        signature: Some(""),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "copy_file_descriptor_to_path",
        args: &[
            ArgInfo { name: "source_fd", r#type: stub_type_int },
            ArgInfo { name: "target_path", r#type: stub_type_path },
            ArgInfo { name: "chunk_size", r#type: stub_type_int },
        ],
        r#return: stub_type_int,
        doc: "",
        signature: Some("source_fd: int, target_path: pathlib.Path, chunk_size: int = ..."),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "encryption_status",
        args: &[ArgInfo { name: "path", r#type: stub_type_path }],
        r#return: stub_type_str,
        doc: "",
        signature: Some("path: pathlib.Path"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "is_lx_encrypted_file",
        args: &[ArgInfo { name: "path", r#type: stub_type_path }],
        r#return: stub_type_bool,
        doc: "",
        signature: Some("path: pathlib.Path"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "decrypt_encrypted_file_range",
        args: &[
            ArgInfo { name: "path", r#type: stub_type_path },
            ArgInfo { name: "master_key", r#type: stub_type_bytes },
            ArgInfo { name: "start", r#type: stub_type_int },
            ArgInfo { name: "end", r#type: stub_type_int },
        ],
        r#return: stub_type_bytes,
        doc: "",
        signature: Some("path: pathlib.Path, master_key: bytes, start: int, end: int"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "sha256_file_hex",
        args: &[
            ArgInfo { name: "path", r#type: stub_type_path },
            ArgInfo { name: "chunk_size", r#type: stub_type_int },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("path: pathlib.Path, chunk_size: int = ..."),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "stable_file_identity",
        args: &[
            ArgInfo { name: "path", r#type: stub_type_path },
            ArgInfo { name: "chunk_size", r#type: stub_type_int },
        ],
        r#return: stub_type_file_identity,
        doc: "",
        signature: Some("path: pathlib.Path, chunk_size: int = ..."),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "stable_snapshot_to_path",
        args: &[
            ArgInfo { name: "source_path", r#type: stub_type_path },
            ArgInfo { name: "target_path", r#type: stub_type_path },
            ArgInfo { name: "chunk_size", r#type: stub_type_int },
        ],
        r#return: stub_type_file_identity,
        doc: "",
        signature: Some(
            "source_path: pathlib.Path, target_path: pathlib.Path, chunk_size: int = ..."
        ),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "render_single_page_pdf",
        args: &[ArgInfo { name: "text", r#type: stub_type_str }],
        r#return: stub_type_bytes,
        doc: "",
        signature: Some("text: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "parse_extracted_frame_numbers",
        args: &[ArgInfo { name: "paths", r#type: stub_type_list_str }],
        r#return: stub_type_list_int,
        doc: "",
        signature: Some("paths: list[str]"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "build_frame_records",
        args: &[
            ArgInfo { name: "paths", r#type: stub_type_list_str },
            ArgInfo { name: "relative_to", r#type: stub_type_str },
            ArgInfo { name: "zero_based", r#type: stub_type_bool },
        ],
        r#return: stub_type_frame_records,
        doc: "",
        signature: Some("paths: list[str], *, relative_to: str | None = ..., zero_based: bool = ..."),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "build_expected_frame_records",
        args: &[
            ArgInfo { name: "frame_count", r#type: stub_type_int },
            ArgInfo { name: "ext", r#type: stub_type_str },
        ],
        r#return: stub_type_frame_records,
        doc: "",
        signature: Some("frame_count: int, ext: str = ..."),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_anonymization_status",
        args: &[
            ArgInfo { name: "processing_error", r#type: stub_type_bool },
            ArgInfo { name: "anonymization_validated", r#type: stub_type_bool },
            ArgInfo { name: "sensitive_meta_processed", r#type: stub_type_bool },
            ArgInfo { name: "frames_extracted", r#type: stub_type_bool },
            ArgInfo { name: "anonymized", r#type: stub_type_bool },
            ArgInfo { name: "was_created", r#type: stub_type_bool },
            ArgInfo { name: "processing_started", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("processing_error: bool, anonymization_validated: bool, sensitive_meta_processed: bool, frames_extracted: bool, anonymized: bool, was_created: bool, processing_started: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_report_anonymization_status",
        args: &[
            ArgInfo { name: "processing_error", r#type: stub_type_bool },
            ArgInfo { name: "anonymization_validated", r#type: stub_type_bool },
            ArgInfo { name: "sensitive_meta_processed", r#type: stub_type_bool },
            ArgInfo { name: "anonymized", r#type: stub_type_bool },
            ArgInfo { name: "processing_started", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("processing_error: bool, anonymization_validated: bool, sensitive_meta_processed: bool, anonymized: bool, processing_started: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_hls_reservation_action",
        args: &[
            ArgInfo { name: "active_status", r#type: stub_type_str },
            ArgInfo { name: "active_is_stale", r#type: stub_type_bool },
            ArgInfo { name: "ready_matches_source", r#type: stub_type_bool },
            ArgInfo { name: "force", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("active_status: str, active_is_stale: bool, ready_matches_source: bool, force: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_hls_publication_action",
        args: &[
            ArgInfo { name: "attempt_status", r#type: stub_type_str },
            ArgInfo { name: "owner_matches", r#type: stub_type_bool },
            ArgInfo { name: "has_active_lease", r#type: stub_type_bool },
            ArgInfo { name: "has_ready_generation", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("attempt_status: str, owner_matches: bool, has_active_lease: bool, has_ready_generation: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_hls_reconciliation_action",
        args: &[
            ArgInfo { name: "status", r#type: stub_type_str },
            ArgInfo { name: "is_stale", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("status: str, is_stale: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_segment_annotation_status",
        args: &[
            ArgInfo { name: "segment_annotations_created", r#type: stub_type_bool },
            ArgInfo { name: "segment_annotations_validated", r#type: stub_type_bool },
            ArgInfo { name: "outside_segments_removed", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("segment_annotations_created: bool, segment_annotations_validated: bool, outside_segments_removed: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "derive_frame_annotation_status",
        args: &[
            ArgInfo { name: "has_state", r#type: stub_type_bool },
            ArgInfo { name: "frames_extracted", r#type: stub_type_bool },
            ArgInfo { name: "initial_prediction_completed", r#type: stub_type_bool },
            ArgInfo { name: "lvs_created", r#type: stub_type_bool },
            ArgInfo { name: "frame_annotations_generated", r#type: stub_type_bool },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("has_state: bool, frames_extracted: bool, initial_prediction_completed: bool, lvs_created: bool, frame_annotations_generated: bool"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "normalize_frame_task_mode_token",
        args: &[ArgInfo { name: "value", r#type: stub_type_str }],
        r#return: stub_type_str,
        doc: "",
        signature: Some("value: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "normalize_frame_sampling_strategy_token",
        args: &[ArgInfo { name: "value", r#type: stub_type_str }],
        r#return: stub_type_str,
        doc: "",
        signature: Some("value: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "storage_profile_policy_rows",
        args: &[],
        r#return: stub_type_storage_policy_rows,
        doc: "",
        signature: Some(""),
        module: None,
    }
}

define_stub_info_gatherer!(stub_info);
