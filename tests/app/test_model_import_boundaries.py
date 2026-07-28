import ast
from collections import defaultdict
from pathlib import Path


ImportKey = tuple[str, str]
ImportMap = dict[ImportKey, frozenset[str]]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "endoreg_db"
MODELS_ROOT = PROJECT_ROOT / "endoreg_db" / "models"
SERVICES_ROOT = PROJECT_ROOT / "endoreg_db" / "services"
SERIALIZERS_ROOT = PROJECT_ROOT / "endoreg_db" / "serializers"
VIEWS_ROOT = PROJECT_ROOT / "endoreg_db" / "views"
UTILS_ROOT = PROJECT_ROOT / "endoreg_db" / "utils"
HELPERS_ROOT = PROJECT_ROOT / "endoreg_db" / "helpers"
MANAGEMENT_COMMANDS_ROOT = PROJECT_ROOT / "endoreg_db" / "management" / "commands"
SERVICE_IMPORT_PREFIX = "endoreg_db.services"
MODEL_BARREL_IMPORT_PREFIX = "endoreg_db.models"
BANNED_MODEL_REEXPORT_IMPORT_MODULES = (
    MODEL_BARREL_IMPORT_PREFIX,
    "endoreg_db.models.aidataset",
    "endoreg_db.models.metadata",
    "endoreg_db.models.state",
    "endoreg_db.models.label.label_video_segment",
    "endoreg_db.models.media.frame",
    "endoreg_db.models.medical.hardware",
)
BANNED_MODEL_IMPLEMENTATION_IMPORT_PREFIXES = (
    "endoreg_db.models.media.pdf.create_report_from_file",
    "endoreg_db.models.media.video.create_from_file",
    "endoreg_db.models.media.video.video_file_ai",
    "endoreg_db.models.media.video.video_file_anonymize",
    "endoreg_db.models.media.video.video_file_frames",
    "endoreg_db.models.media.video.video_file_io",
    "endoreg_db.models.media.video.video_file_meta",
    "endoreg_db.models.media.video.video_file_segments",
    "endoreg_db.models.media.video.video_file_streaming",
    "endoreg_db.models.media.video.video_file_time",
)
BANNED_MODEL_BARREL_IMPORT_ROOTS = (
    SERVICES_ROOT,
    SERIALIZERS_ROOT,
    VIEWS_ROOT,
    UTILS_ROOT,
    HELPERS_ROOT,
    MANAGEMENT_COMMANDS_ROOT,
)
ALLOWLISTED_MODEL_BARREL_IMPORTS: ImportMap = {}
VIEW_MODEL_WORKFLOW_IMPORT_MODULES = ("endoreg_db.models.state.frame_annotation",)
ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS: ImportMap = {}

ALLOWLISTED_MODEL_TO_SERVICE_IMPORTS = {
    (
        "endoreg_db/models/administration/ai/ai_model.py",
        "endoreg_db.services.model_meta_from_hf",
    ): frozenset({"ensure_model_meta_from_hf"}),
    (
        "endoreg_db/models/aidataset/aidataset.py",
        "endoreg_db.services.aidataset_active_learning",
    ): frozenset(
        {
            "select_active_learning_candidates_locally",
        }
    ),
    (
        "endoreg_db/models/aidataset/aidataset.py",
        "endoreg_db.services.aidataset_exports",
    ): frozenset(
        {
            "build_export_payload",
            "export_to_standardized_structure",
        }
    ),
    (
        "endoreg_db/models/aidataset/aidataset.py",
        "endoreg_db.services.aidataset_frame_buckets",
    ): frozenset(
        {
            "build_frame_bucket_distribution",
        }
    ),
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
            "set_video_frame_dir",
            "update_video_meta",
            "update_video_text_metadata",
            "validate_video_metadata_annotation",
            "video_frame_number_to_seconds",
        }
    ),
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


def _imported_name(alias: ast.alias) -> str:
    if alias.asname is None:
        return alias.name
    return f"{alias.name} as {alias.asname}"


def _model_to_service_imports() -> ImportMap:
    imports: defaultdict[ImportKey, set[str]] = defaultdict(set)

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


def _is_banned_model_reexport_import(module: str) -> bool:
    return module in BANNED_MODEL_REEXPORT_IMPORT_MODULES


def _is_view_model_workflow_import(module: str) -> bool:
    return module in VIEW_MODEL_WORKFLOW_IMPORT_MODULES


def _service_to_model_implementation_imports() -> ImportMap:
    imports: defaultdict[ImportKey, set[str]] = defaultdict(set)

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


def _view_model_workflow_imports() -> ImportMap:
    imports: defaultdict[ImportKey, set[str]] = defaultdict(set)

    for path in sorted(VIEWS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and _is_view_model_workflow_import(node.module)
            ):
                imports[(rel_path, node.module)].update(
                    _imported_name(alias) for alias in node.names
                )
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_view_model_workflow_import(alias.name):
                        imports[(rel_path, alias.name)].add(_imported_name(alias))

    return {key: frozenset(names) for key, names in imports.items()}


def _runtime_model_barrel_imports() -> ImportMap:
    imports: defaultdict[ImportKey, set[str]] = defaultdict(set)

    for root in BANNED_MODEL_BARREL_IMPORT_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            rel_path = path.relative_to(PROJECT_ROOT).as_posix()

            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and _is_banned_model_reexport_import(node.module)
                ):
                    imports[(rel_path, node.module)].update(
                        _imported_name(alias) for alias in node.names
                    )
                    continue

                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _is_banned_model_reexport_import(alias.name):
                            imports[(rel_path, alias.name)].add(_imported_name(alias))

    return {key: frozenset(names) for key, names in imports.items()}


def _project_to_model_implementation_references() -> ImportMap:
    references: defaultdict[ImportKey, set[str]] = defaultdict(set)

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


def test_models_do_not_add_new_service_imports() -> None:
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


def test_services_do_not_import_model_media_implementation_modules() -> None:
    assert not _service_to_model_implementation_imports()


def test_runtime_layers_do_not_use_model_barrel_imports() -> None:
    current_imports = _runtime_model_barrel_imports()

    unexpected_import_keys = set(current_imports) - set(
        ALLOWLISTED_MODEL_BARREL_IMPORTS
    )
    stale_import_keys = set(ALLOWLISTED_MODEL_BARREL_IMPORTS) - set(current_imports)
    changed_import_names = {
        key: {
            "unexpected": sorted(
                current_imports[key] - ALLOWLISTED_MODEL_BARREL_IMPORTS[key]
            ),
            "stale": sorted(
                ALLOWLISTED_MODEL_BARREL_IMPORTS[key] - current_imports[key]
            ),
        }
        for key in set(current_imports) & set(ALLOWLISTED_MODEL_BARREL_IMPORTS)
        if current_imports[key] != ALLOWLISTED_MODEL_BARREL_IMPORTS[key]
    }

    assert not unexpected_import_keys, sorted(unexpected_import_keys)
    assert not stale_import_keys, sorted(stale_import_keys)
    assert not changed_import_names, changed_import_names


def test_views_do_not_add_model_workflow_imports() -> None:
    current_imports = _view_model_workflow_imports()

    unexpected_import_keys = set(current_imports) - set(
        ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS
    )
    stale_import_keys = set(ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS) - set(
        current_imports
    )
    changed_import_names = {
        key: {
            "unexpected": sorted(
                current_imports[key] - ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS[key]
            ),
            "stale": sorted(
                ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS[key] - current_imports[key]
            ),
        }
        for key in set(current_imports) & set(ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS)
        if current_imports[key] != ALLOWLISTED_VIEW_MODEL_WORKFLOW_IMPORTS[key]
    }

    assert not unexpected_import_keys, sorted(unexpected_import_keys)
    assert not stale_import_keys, sorted(stale_import_keys)
    assert not changed_import_names, changed_import_names


def test_project_code_does_not_reference_model_media_implementation_modules() -> None:
    assert not _project_to_model_implementation_references()
