use pyo3_stub_gen::{
    define_stub_info_gatherer, inventory,
    type_info::{ArgInfo, PyFunctionInfo},
    TypeInfo,
};

fn stub_type_status_rules() -> TypeInfo {
    TypeInfo::builtin("list[tuple[str, list[tuple[str, bool]]]]")
}

inventory::submit! {
    PyFunctionInfo {
        name: "anonymization_status_rules",
        args: &[ArgInfo { name: "report", r#type: stub_type_bool }],
        r#return: stub_type_status_rules,
        doc: "Ordered rules shared by native state derivation and SQL aggregation.",
        signature: Some("report: bool"),
        module: None,
    }
}

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

fn stub_type_hub_decision() -> TypeInfo {
    TypeInfo::builtin("tuple[str, str | None, bool]")
}

inventory::submit! {
    PyFunctionInfo {
        name: "reduce_hub_transfer_event",
        args: &[
            ArgInfo { name: "current_state", r#type: stub_type_str },
            ArgInfo { name: "current_failure_class", r#type: stub_type_optional_str },
            ArgInfo { name: "event", r#type: stub_type_str },
            ArgInfo { name: "remote_status", r#type: stub_type_optional_str },
            ArgInfo { name: "failure_class", r#type: stub_type_optional_str },
        ],
        r#return: stub_type_hub_decision,
        doc: "Native hub transfer transition; callers hold the row lock and write only when applied.",
        signature: Some("current_state: str, current_failure_class: str | None, event: str, remote_status: str | None = None, failure_class: str | None = None"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "hub_failure_is_retryable",
        args: &[ArgInfo { name: "failure_class", r#type: stub_type_str }],
        r#return: stub_type_bool,
        doc: "Native retry classification; unknown failure categories raise.",
        signature: Some("failure_class: str"),
        module: None,
    }
}

fn stub_type_state_vocabularies() -> TypeInfo {
    TypeInfo::builtin("list[tuple[str, list[str]]]")
}

inventory::submit! {
    PyFunctionInfo {
        name: "native_state_vocabularies",
        args: &[],
        r#return: stub_type_state_vocabularies,
        doc: "Every native state, event and action vocabulary; Python mirrors are verified against it.",
        signature: Some(""),
        module: None,
    }
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
        name: "stable_file_identity",
        args: &[
            ArgInfo { name: "path", r#type: stub_type_path },
            ArgInfo { name: "chunk_size", r#type: stub_type_int },
            ArgInfo { name: "master_keys", r#type: stub_type_list_bytes },
        ],
        r#return: stub_type_file_identity,
        doc: "",
        signature: Some("path: pathlib.Path, chunk_size: int = ..., master_keys: list[bytes] = ..."),
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
        name: "anonymization_status_phase",
        args: &[ArgInfo { name: "status", r#type: stub_type_str }],
        r#return: stub_type_str,
        doc: "",
        signature: Some("status: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "decide_status_transition",
        args: &[
            ArgInfo { name: "machine", r#type: stub_type_str },
            ArgInfo { name: "current", r#type: stub_type_str },
            ArgInfo { name: "target", r#type: stub_type_str },
        ],
        r#return: stub_type_bool,
        doc: "",
        signature: Some("machine: str, current: str, target: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "decide_hls_artifact_transition",
        args: &[
            ArgInfo { name: "current_status", r#type: stub_type_str },
            ArgInfo { name: "target_status", r#type: stub_type_str },
        ],
        r#return: stub_type_bool,
        doc: "",
        signature: Some("current_status: str, target_status: str"),
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
        name: "transition_service_lifecycle",
        args: &[
            ArgInfo { name: "current_state", r#type: stub_type_str },
            ArgInfo { name: "event", r#type: stub_type_str },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("current_state: str, event: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "transition_operation_lifecycle",
        args: &[
            ArgInfo { name: "current_state", r#type: stub_type_str },
            ArgInfo { name: "event", r#type: stub_type_str },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: Some("current_state: str, event: str"),
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

fn stub_type_list_list_int() -> TypeInfo {
    TypeInfo::builtin("list[list[int]]")
}

inventory::submit! {
    PyFunctionInfo {
        name: "compose_frame_ancestry",
        args: &[
            ArgInfo { name: "source_frame_counts", r#type: stub_type_list_int },
            ArgInfo { name: "source_frame_indices", r#type: stub_type_list_list_int },
        ],
        r#return: stub_type_list_int,
        doc: "Map each final output frame to its original-recording frame index.",
        signature: Some("source_frame_counts: list[int], source_frame_indices: list[list[int]]"),
        module: None,
    }
}

define_stub_info_gatherer!(stub_info);

inventory::submit! {
    PyFunctionInfo {
        name: "new_import_processing_stack",
        args: &[ArgInfo { name: "reference", r#type: stub_type_str }, ArgInfo { name: "media_type", r#type: stub_type_str }],
        r#return: stub_type_str, doc: "Create a durable import processing stack.", signature: Some("reference: str, media_type: str"), module: None,
    }
}
fn stub_type_optional_str() -> TypeInfo {
    TypeInfo::builtin("str | None")
}
fn stub_type_optional_int() -> TypeInfo {
    TypeInfo::builtin("int | None")
}
inventory::submit! {
    PyFunctionInfo {
        name: "transition_import_processing_stack",
        args: &[ArgInfo { name: "payload", r#type: stub_type_str }, ArgInfo { name: "event", r#type: stub_type_str }, ArgInfo { name: "media_id", r#type: stub_type_optional_int }, ArgInfo { name: "source_sha256", r#type: stub_type_optional_str }],
        r#return: stub_type_str, doc: "Validate and advance a durable import processing stack.", signature: Some("payload: str, event: str, media_id: int | None = None, source_sha256: str | None = None"), module: None,
    }
}

fn stub_type_list_bytes() -> TypeInfo {
    TypeInfo::builtin("list[bytes]")
}
fn stub_type_header() -> TypeInfo {
    TypeInfo::builtin("tuple[int, str, int, bytes, bytes, bytes]")
}
fn stub_type_header_metadata() -> TypeInfo {
    TypeInfo::builtin("tuple[int, str, int, bytes, bytes, bytes, bytes | None, bytes | None]")
}
inventory::submit! {
    PyFunctionInfo {
        name: "parse_encrypted_header",
        args: &[ArgInfo { name: "data", r#type: stub_type_bytes }],
        r#return: stub_type_header,
        doc: "Validate the shared encrypted header contract.",
        signature: Some("data: bytes"),
        module: None,
    }
}

fn stub_type_chunk_context() -> TypeInfo {
    TypeInfo::builtin("tuple[bytes, bytes]")
}
inventory::submit! {
    PyFunctionInfo {
        name: "parse_encrypted_header_metadata",
        args: &[ArgInfo { name: "data", r#type: stub_type_bytes }],
        r#return: stub_type_header_metadata,
        doc: "Validate the shared encrypted header contract including versioned identifiers.",
        signature: Some("data: bytes"),
        module: None,
    }
}
inventory::submit! {
    PyFunctionInfo {
        name: "encrypted_chunk_context",
        args: &[
            ArgInfo { name: "data", r#type: stub_type_bytes },
            ArgInfo { name: "counter", r#type: stub_type_int },
            ArgInfo { name: "ciphertext_length", r#type: stub_type_int },
            ArgInfo { name: "final", r#type: stub_type_bool },
        ],
        r#return: stub_type_chunk_context,
        doc: "Validate chunk geometry and return the shared nonce and associated data.",
        signature: Some("data: bytes, counter: int, ciphertext_length: int, final: bool"),
        module: None,
    }
}

fn stub_type_clinical_state() -> TypeInfo {
    TypeInfo::builtin("tuple[str, str, str]")
}

fn stub_type_clinical_projection() -> TypeInfo {
    TypeInfo::builtin("tuple[tuple[str, str, str], list[str]]")
}

fn stub_type_object() -> TypeInfo {
    TypeInfo::builtin("object")
}

inventory::submit! {
    PyFunctionInfo {
        name: "reduce_clinical_workflow",
        args: &[
            ArgInfo { name: "privacy", r#type: stub_type_str },
            ArgInfo { name: "annotation", r#type: stub_type_str },
            ArgInfo { name: "release", r#type: stub_type_str },
            ArgInfo { name: "event", r#type: stub_type_str },
        ],
        r#return: stub_type_clinical_state,
        doc: "Decide a clinical workflow event.",
        signature: Some("privacy: str, annotation: str, release: str, event: str"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "clinical_evidence_rejection",
        args: &[
            ArgInfo { name: "event", r#type: stub_type_str },
            ArgInfo { name: "identity_verified", r#type: stub_type_bool },
            ArgInfo { name: "report", r#type: stub_type_bool },
        ],
        r#return: stub_type_optional_str,
        doc: "Return the rejection token for missing event evidence.",
        signature: Some("event: str, identity_verified: bool, report: bool = False"),
        module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "project_clinical_workflow",
        args: &[
            ArgInfo { name: "facts", r#type: stub_type_object },
        ],
        r#return: stub_type_clinical_projection,
        doc: "Project persisted clinical facts and report all invariant violations.",
        signature: Some("facts: object"),
        module: None,
    }
}

fn stub_type_clinical_tables() -> TypeInfo {
    TypeInfo::builtin("tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str, str]]]")
}

inventory::submit! {
    PyFunctionInfo {
        name: "clinical_workflow_tables",
        args: &[],
        r#return: stub_type_clinical_tables,
        doc: "Enumerate the single-track edges accepted by the clinical reducer.",
        signature: Some(""), module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "hub_export_integrity",
        args: &[
            ArgInfo { name: "present", r#type: stub_type_bool },
            ArgInfo { name: "approved", r#type: stub_type_bool },
            ArgInfo { name: "media_digest", r#type: stub_type_str },
            ArgInfo { name: "approval_digest", r#type: stub_type_str },
            ArgInfo { name: "verify", r#type: stub_type_bool },
            ArgInfo { name: "actual", r#type: stub_type_optional_str },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: None, module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "raw_video_cleanup_blockers",
        args: &[
            ArgInfo { name: "active_lease", r#type: stub_type_bool },
            ArgInfo { name: "normalization_profile", r#type: stub_type_optional_str },
            ArgInfo { name: "source_timeline_present", r#type: stub_type_bool },
            ArgInfo { name: "quality_profile", r#type: stub_type_optional_str },
            ArgInfo { name: "hls_source", r#type: stub_type_optional_str },
            ArgInfo { name: "processed_source", r#type: stub_type_str },
            ArgInfo { name: "segment_timeline_valid", r#type: stub_type_bool },
        ],
        r#return: stub_type_list_str,
        doc: "",
        signature: None, module: None,
    }
}

inventory::submit! {
    PyFunctionInfo {
        name: "segment_status_with_history",
        args: &[
            ArgInfo { name: "created", r#type: stub_type_bool },
            ArgInfo { name: "validated", r#type: stub_type_bool },
            ArgInfo { name: "outside_removed", r#type: stub_type_bool },
            ArgInfo { name: "history", r#type: stub_type_optional_str },
        ],
        r#return: stub_type_str,
        doc: "",
        signature: None, module: None,
    }
}

fn stub_type_identifier_clear_values() -> TypeInfo {
    TypeInfo::builtin("list[tuple[str, str | None]]")
}

inventory::submit! {
    PyFunctionInfo {
        name: "direct_identifier_clear_values",
        args: &[ArgInfo { name: "record", r#type: stub_type_object }],
        r#return: stub_type_identifier_clear_values,
        doc: "Return the schema fields that still contain direct identifiers and their cleared representations.",
        signature: None, module: None,
    }
}
