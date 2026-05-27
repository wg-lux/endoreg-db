import ast
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "endoreg_db"
MODELS_ROOT = PROJECT_ROOT / "endoreg_db" / "models"
SERVICES_ROOT = PROJECT_ROOT / "endoreg_db" / "services"
SERVICE_IMPORT_PREFIX = "endoreg_db.services"
BANNED_MODEL_IMPLEMENTATION_IMPORT_PREFIXES = (
    "endoreg_db.models.media.pdf.create_report_from_file",
    "endoreg_db.models.media.video.create_from_file",
    "endoreg_db.models.media.video.pipe_1",
    "endoreg_db.models.media.video.pipe_2",
    "endoreg_db.models.media.video.video_file_ai",
    "endoreg_db.models.media.video.video_file_anonymize",
    "endoreg_db.models.media.video.video_file_frames",
    "endoreg_db.models.media.video.video_file_io",
    "endoreg_db.models.media.video.video_file_meta",
    "endoreg_db.models.media.video.video_file_segments",
    "endoreg_db.models.media.video.video_file_streaming",
    "endoreg_db.models.media.video.video_file_time",
)

ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS = {
    (
        "endoreg_db/models/administration/ai/ai_model.py",
        "endoreg_db.services.model_meta_from_hf",
    ): frozenset({"ensure_model_meta_from_hf"}),
    (
        "endoreg_db/models/aidataset/aidataset.py",
        "endoreg_db.services.aidataset_exports",
    ): frozenset(
        {
            "AIDataSetExportPayload",
            "AIDataSetExportSummary",
            "AIDataSetFrameAnnotationExport",
            "AIDataSetFrameLabelExport",
            "build_export_payload",
            "export_to_standardized_structure",
        }
    ),
    (
        "endoreg_db/models/aidataset/aidataset.py",
        "endoreg_db.services.aidataset_frame_buckets",
    ): frozenset(
        {
            "AIDataSetFrameBucketCount",
            "AIDataSetFrameBucketDistribution",
            "AIDataSetFrameBucketSummary",
            "AIDataSetLabelDistributionEntry",
            "AIDataSetLabelFrameBucketCount",
            "AIDataSetTargetFrameBucket",
            "build_frame_bucket_distribution",
        }
    ),
    (
        "endoreg_db/models/hub/transfer_job.py",
        "endoreg_db.services.hub.payloads",
    ): frozenset({"validate_transfer_provenance_payload"}),
    (
        "endoreg_db/models/hub/upload_job.py",
        "endoreg_db.services.hub.payloads",
    ): frozenset({"validate_upload_provenance_payload"}),
    (
        "endoreg_db/models/label/label_video_segment/label_video_segment.py",
        "endoreg_db.services.video_files",
    ): frozenset(
        {
            "delete_video_frame_range",
            "extract_video_frame_range",
            "get_video_fps",
        }
    ),
    (
        "endoreg_db/models/media/pdf/create_report_from_file.py",
        "endoreg_db.services.raw_pdf_files",
    ): frozenset({"create_raw_pdf_file_from_path"}),
    (
        "endoreg_db/models/media/pdf/raw_pdf.py",
        "endoreg_db.services.raw_pdf_files",
    ): frozenset(
        {
            "build_report_reader_config",
            "create_initialized_raw_pdf_file_from_path",
            "create_raw_pdf_file_from_path",
            "delete_raw_pdf_with_owned_files",
            "get_or_create_raw_pdf_state",
            "get_processed_pdf_file_url",
            "get_processed_pdf_plaintext_path",
            "get_raw_pdf_by_content_hash",
            "get_raw_pdf_by_pk",
            "get_raw_pdf_file_path",
            "get_raw_pdf_file_url",
            "get_raw_pdf_plaintext_path",
            "initialize_raw_pdf_file",
            "mark_report_sensitive_meta_processed",
            "mark_report_sensitive_meta_verified",
            "prepare_raw_pdf_before_save",
            "process_raw_pdf_file",
            "set_processed_pdf_file_path",
            "set_raw_pdf_file_path",
            "validate_report_metadata_annotation",
            "verify_existing_raw_pdf_file",
        }
    ),
    (
        "endoreg_db/models/media/video/video_file.py",
        "endoreg_db.services.video_files",
    ): frozenset(
        {
            "anonymize_video_file",
            "bulk_create_video_frames",
            "can_offload_video_stream",
            "cleanup_video_raw_assets",
            "count_unmodified_other_videos",
            "create_anonymized_video_frame_files",
            "create_initialized_video_file_from_path",
            "create_video_file_from_path",
            "create_video_frame_object",
            "delete_video_frame_range",
            "delete_video_frames",
            "delete_video_with_owned_files",
            "ensure_default_video_fps",
            "ensure_local_processed_video_file",
            "ensure_local_raw_video_file",
            "extract_text_from_video_frames",
            "extract_video_frame_range",
            "extract_video_frames",
            "get_active_raw_video_file",
            "get_active_raw_video_file_url",
            "get_active_video_file",
            "get_active_video_file_path",
            "get_active_video_file_url",
            "get_all_videos",
            "get_or_create_video_state",
            "get_processed_video_file_path",
            "get_processed_video_stream_path",
            "get_processed_video_stream_relative_path",
            "get_protected_video_stream_url",
            "get_raw_video_file_path",
            "get_raw_video_stream_path",
            "get_raw_video_stream_relative_path",
            "get_target_anonymized_video_path",
            "get_temp_anonymized_video_frame_dir",
            "get_video_base_frame_dir",
            "get_video_by_content_hash",
            "get_video_by_pk",
            "get_video_crop_template",
            "get_video_duration",
            "get_video_endo_roi",
            "get_video_ffmpeg_meta",
            "get_video_fps",
            "get_video_frame",
            "get_video_frame_dir_path",
            "get_video_frame_number",
            "get_video_frame_path",
            "get_video_frame_paths",
            "get_video_frame_range",
            "get_video_frames",
            "get_video_outside_segments",
            "get_video_stream_relative_path",
            "initialize_video_file",
            "initialize_video_frames",
            "initialize_video_specs",
            "is_encrypted_streamable_video_path",
            "parse_video_artifact_kind",
            "predict_video",
            "rebuild_processed_video_without_outside_frames",
            "resolve_video_stream_source",
            "run_video_pipe_1",
            "run_video_pipe_2",
            "set_video_frame_dir",
            "test_after_video_pipe_1",
            "update_video_meta",
            "update_video_text_metadata",
            "validate_video_metadata_annotation",
            "video_frame_number_to_seconds",
            "video_hash_exists",
        }
    ),
    (
        "endoreg_db/models/media/video/video_file.py",
        "endoreg_db.services.video_post_validation_blackening",
    ): frozenset({"merge_outside_frame_intervals"}),
    (
        "endoreg_db/models/medical/patient/patient_examination.py",
        "endoreg_db.services.knowledge_base_identity",
    ): frozenset({"get_configured_knowledge_base_identity"}),
    (
        "endoreg_db/models/metadata/video_prediction_logic.py",
        "endoreg_db.services.video_files",
    ): frozenset({"get_video_fps"}),
    (
        "endoreg_db/models/metadata/video_prediction_meta.py",
        "endoreg_db.services.video_files",
    ): frozenset({"get_video_fps"}),
    (
        "endoreg_db/models/state/frame_annotation.py",
        "endoreg_db.services.video_files",
    ): frozenset({"get_or_create_video_state"}),
    (
        "endoreg_db/models/state/video_segment_validation.py",
        "endoreg_db.services.video_files",
    ): frozenset({"get_or_create_video_state"}),
}


def _imported_name(alias):
    if alias.asname is None:
        return alias.name
    return f"{alias.name} as {alias.asname}"


def _model_to_service_imports():
    imports = defaultdict(set)

    for path in sorted(MODELS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith(SERVICE_IMPORT_PREFIX)
            ):
                imports[(rel_path, node.module)].update(
                    _imported_name(alias) for alias in node.names
                )
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(SERVICE_IMPORT_PREFIX):
                        imports[(rel_path, alias.name)].add(_imported_name(alias))

    return {key: frozenset(names) for key, names in imports.items()}


def _is_banned_model_implementation_import(module: str) -> bool:
    return any(
        module == banned_prefix or module.startswith(f"{banned_prefix}.")
        for banned_prefix in BANNED_MODEL_IMPLEMENTATION_IMPORT_PREFIXES
    )


def _service_to_model_implementation_imports():
    imports = defaultdict(set)

    for path in sorted(SERVICES_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and _is_banned_model_implementation_import(node.module)
            ):
                imports[(rel_path, node.module)].update(
                    _imported_name(alias) for alias in node.names
                )
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_banned_model_implementation_import(alias.name):
                        imports[(rel_path, alias.name)].add(_imported_name(alias))

    return {key: frozenset(names) for key, names in imports.items()}


def _project_to_model_implementation_references():
    references = defaultdict(set)

    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and _is_banned_model_implementation_import(node.module)
            ):
                references[(rel_path, "import")].update(
                    _imported_name(alias) for alias in node.names
                )
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_banned_model_implementation_import(alias.name):
                        references[(rel_path, "import")].add(_imported_name(alias))
                continue

            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _is_banned_model_implementation_import(node.value)
            ):
                references[(rel_path, "string")].add(node.value)

    return {key: frozenset(names) for key, names in references.items()}


def test_models_do_not_add_new_service_imports():
    current_imports = _model_to_service_imports()

    unexpected_import_keys = set(current_imports) - set(
        ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS
    )
    stale_import_keys = set(ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS) - set(current_imports)
    changed_import_names = {
        key: {
            "unexpected": sorted(
                current_imports[key] - ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS[key]
            ),
            "stale": sorted(
                ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS[key] - current_imports[key]
            ),
        }
        for key in set(current_imports) & set(ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS)
        if current_imports[key] != ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS[key]
    }

    assert not unexpected_import_keys, sorted(unexpected_import_keys)
    assert not stale_import_keys, sorted(stale_import_keys)
    assert not changed_import_names, changed_import_names


def test_services_do_not_import_model_media_implementation_modules():
    assert not _service_to_model_implementation_imports()


def test_project_code_does_not_reference_model_media_implementation_modules():
    assert not _project_to_model_implementation_references()
