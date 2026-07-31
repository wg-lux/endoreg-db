import ast
from collections import defaultdict
from collections.abc import Iterable
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
SCHEMAS_ROOT = PROJECT_ROOT / "endoreg_db" / "schemas"
SERVICE_IMPORT_PREFIX = "endoreg_db.services"
MODEL_BARREL_IMPORT_PREFIX = "endoreg_db.models"
BANNED_MODEL_REEXPORT_IMPORT_MODULES = (
    MODEL_BARREL_IMPORT_PREFIX,
    "endoreg_db.models.aidataset",
    "endoreg_db.models.label",
    "endoreg_db.models.metadata",
    "endoreg_db.models.other",
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
MODEL_BARREL_CLEAN_ROOTS = (MODELS_ROOT / "label" / "label_video_segment",)
MODEL_BARREL_CLEAN_FILES = (
    MODELS_ROOT / "metadata" / "video_prediction_logic.py",
    MODELS_ROOT / "metadata" / "video_prediction_meta.py",
    MODELS_ROOT / "state" / "frame_annotation.py",
    MODELS_ROOT / "state" / "video_segment_validation.py",
)
ALLOWLISTED_MODEL_BARREL_IMPORTS: ImportMap = {}
MODEL_WORKFLOW_IMPORT_MODULES = (
    "endoreg_db.models.state.frame_annotation",
    "endoreg_db.models.state.video_segment_validation",
)
ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS: ImportMap = {}
FRAME_ANNOTATION_MODEL_WORKFLOW_MODULE = "endoreg_db.models.state.frame_annotation"
FRAME_ANNOTATION_SEGMENT_IDENTITY_MODULE = (
    "endoreg_db.models.state.frame_annotation_segment_identity"
)
ALLOWLISTED_SERVICE_FRAME_ANNOTATION_IMPORTS: ImportMap = {
    (
        "endoreg_db/services/frame_annotation_workflow.py",
        FRAME_ANNOTATION_MODEL_WORKFLOW_MODULE,
    ): frozenset(
        {
            "DEFAULT_FRAME_INFORMATION_SOURCE_NAME",
            "SUPPORTED_FRAME_SAMPLING_STRATEGIES",
            "SUPPORTED_FRAME_TASK_MODES",
            "FrameAnnotationQueueResult",
            "FrameAnnotationQueueSpec",
            "FrameAnnotationTaskPayload",
            "FrameLike",
            "FrameSamplingStrategy",
            "FrameTaskMode",
            "RequestLike",
            "ai_dataset_requires_raw_frames as _ai_dataset_requires_raw_frames",
            "build_annotation_frame_buckets",
            "build_balanced_label_order",
            "build_dataset_candidate_frame_ids",
            "build_dataset_label_distribution",
            "build_dataset_target_buckets",
            "build_segment_frame_buckets",
            "mark_frame_prediction_completed",
            "mark_frame_prediction_reset",
            "mark_prediction_segments_created",
            "merge_frame_buckets",
            "normalize_frame_sampling_strategy as _normalize_frame_sampling_strategy",
            "normalize_frame_task_mode as _normalize_frame_task_mode",
            "pick_balanced_dataset_frame",
            "pick_random_frame",
            "resolve_ai_dataset_for_queue as _resolve_ai_dataset_for_queue",
            "resolve_frame_information_source_name as _resolve_frame_information_source_name",
            "resolve_request_annotator as _resolve_request_annotator",
            "serialize_frame_task",
            "serialize_label_distribution",
            "validated_annotators_for_video as _validated_annotators_for_video",
        }
    ),
    (
        "endoreg_db/services/segment_frame_annotations.py",
        FRAME_ANNOTATION_MODEL_WORKFLOW_MODULE,
    ): frozenset(
        {
            "LabelVideoSegmentLike",
            "SegmentAnnotationSnapshot",
            "delete_frame_annotations_for_segment as _delete_frame_annotations_for_segment",
            "sync_frame_annotations_for_segment as _sync_frame_annotations_for_segment",
        }
    ),
}
ALLOWLISTED_MODEL_FRAME_ANNOTATION_IMPORTS: ImportMap = {}
ALLOWLISTED_FRAME_ANNOTATION_SEGMENT_IDENTITY_IMPORTS: ImportMap = {
    (
        "endoreg_db/models/label/label_video_segment/label_video_segment.py",
        FRAME_ANNOTATION_SEGMENT_IDENTITY_MODULE,
    ): frozenset(
        {
            "is_prediction_segment",
            "manual_annotation_filter",
            "manual_frame_annotation_preference_filter",
            "prediction_annotation_filter",
            "segment_derived_external_annotation_id",
        }
    ),
    (
        "endoreg_db/models/media/frame/frame.py",
        FRAME_ANNOTATION_SEGMENT_IDENTITY_MODULE,
    ): frozenset(
        {
            "manual_annotation_filter",
            "prediction_annotation_filter",
        }
    ),
    (
        "endoreg_db/models/state/frame_annotation.py",
        FRAME_ANNOTATION_SEGMENT_IDENTITY_MODULE,
    ): frozenset(
        {
            "is_prediction_segment",
            "is_segment_derived_external_annotation_id",
            "manual_annotation_filter",
            "manual_frame_annotation_preference_filter",
            "prediction_annotation_filter",
            "segment_derived_external_annotation_id",
        }
    ),
    (
        "endoreg_db/services/frame_annotation_segment_identity.py",
        FRAME_ANNOTATION_SEGMENT_IDENTITY_MODULE,
    ): frozenset(
        {
            "MANUAL_ANNOTATION_INFORMATION_SOURCE_NAMES",
            "PREDICTION_INFORMATION_SOURCE_NAMES",
            "SEGMENT_DERIVED_EXTERNAL_ANNOTATION_PREFIX",
            "is_prediction_segment",
            "is_segment_derived_external_annotation_id",
            "manual_annotation_filter",
            "manual_frame_annotation_preference_filter",
            "non_segment_derived_annotation_filter",
            "prediction_annotation_filter",
            "segment_derived_external_annotation_id",
        }
    ),
}
VIDEO_SEGMENT_VALIDATION_MODEL_WORKFLOW_MODULE = (
    "endoreg_db.models.state.video_segment_validation"
)
ALLOWLISTED_SERVICE_VIDEO_SEGMENT_VALIDATION_IMPORTS: ImportMap = {
    (
        "endoreg_db/services/video_segment_validation_workflow.py",
        VIDEO_SEGMENT_VALIDATION_MODEL_WORKFLOW_MODULE,
    ): frozenset(
        {
            "SegmentAnnotationStatus",
            "mark_post_validation_complete",
            "mark_post_validation_incomplete",
            "mark_segment_annotations_complete_without_cleanup",
            "mark_segment_annotations_pending_cleanup",
            "mark_segment_annotations_stale",
        }
    ),
}
ALLOWLISTED_NON_SCHEMA_PYDANTIC_MODELS = {
    (
        "endoreg_db/export/frames/export.py",
        "ExportAnnotations",
    ),
    (
        "endoreg_db/import_files/context/import_context.py",
        "ImportContext",
    ),
    ("endoreg_db/services/jobs/heavy_jobs.py", "HeavyJobDispatchPayload"),
    ("endoreg_db/services/jobs/report_llm_jobs.py", "ReportLlmDispatchResult"),
    (
        "endoreg_db/services/jobs/video_correction_jobs.py",
        "VideoAnonymizationCorrectionJobConfig",
    ),
    (
        "endoreg_db/services/jobs/video_correction_jobs.py",
        "VideoCorrectionRegion",
    ),
    ("endoreg_db/services/jobs/video_correction_jobs.py", "VideoCorrectionRoi"),
    (
        "endoreg_db/services/jobs/video_fps_normalization_jobs.py",
        "FpsNormalizationHistoryConfig",
    ),
    (
        "endoreg_db/services/offline_batch_runner.py",
        "NativeCapabilityRequirement",
    ),
    ("endoreg_db/services/offline_batch_runner.py", "OfflineBatchResourceBudget"),
    ("endoreg_db/services/offline_batch_runner.py", "OfflineBatchRunSummary"),
    ("endoreg_db/services/offline_batch_runner.py", "OfflineBatchRunnerConfig"),
    ("endoreg_db/services/raw_pdf_files/metadata.py", "ReportProcessingPayload"),
    ("endoreg_db/services/video_storage/probes.py", "_FrameProbePayload"),
    ("endoreg_db/services/video_storage/probes.py", "_FrameProbeRow"),
    ("endoreg_db/services/video_storage/probes.py", "_ProbeFormat"),
    ("endoreg_db/services/video_storage/probes.py", "_ProbePayload"),
    ("endoreg_db/services/video_storage/probes.py", "_ProbeStream"),
    ("endoreg_db/utils/links/model_links.py", "ModelLinks"),
    ("endoreg_db/utils/pydantic_models/db_config.py", "DbConfig"),
    ("endoreg_db/views/video/video_timeline.py", "FrameNeighborhoodQuery"),
}

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
        "endoreg_db.services.video_files.frames",
    ): frozenset(
        {
            "delete_video_frame_range",
            "extract_video_frame_range",
        }
    ),
    (
        "endoreg_db/models/label/label_video_segment/label_video_segment.py",
        "endoreg_db.services.video_files.metadata",
    ): frozenset({"get_video_fps"}),
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
        "endoreg_db.services.video_files.metadata",
    ): frozenset({"get_video_fps"}),
    (
        "endoreg_db/models/metadata/video_prediction_meta.py",
        "endoreg_db.services.video_files.metadata",
    ): frozenset({"get_video_fps"}),
    (
        "endoreg_db/models/state/frame_annotation.py",
        "endoreg_db.services.video_files.state",
    ): frozenset({"get_or_create_video_state"}),
    (
        "endoreg_db/models/state/video_segment_validation.py",
        "endoreg_db.services.video_files.state",
    ): frozenset({"get_or_create_video_state"}),
}


def _imported_name(alias: ast.alias) -> str:
    if alias.asname is None:
        return alias.name
    return f"{alias.name} as {alias.asname}"


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = path.parent.relative_to(PROJECT_ROOT).parts
    parent_hops = node.level - 1
    if parent_hops > len(package_parts):
        return ""
    base_parts = package_parts[: len(package_parts) - parent_hops]
    module_parts = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*base_parts, *module_parts))


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


def _model_workflow_imports(
    paths: Iterable[Path],
    workflow_modules: frozenset[str],
) -> ImportMap:
    imports: defaultdict[ImportKey, set[str]] = defaultdict(set)

    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_module = _resolve_import_from_module(path, node)
            else:
                imported_module = ""
            if (
                isinstance(node, ast.ImportFrom)
                and imported_module
                and imported_module in workflow_modules
            ):
                imports[(rel_path, imported_module)].update(
                    _imported_name(alias) for alias in node.names
                )
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in workflow_modules:
                        imports[(rel_path, alias.name)].add(_imported_name(alias))

    return {key: frozenset(names) for key, names in imports.items()}


def _runtime_model_workflow_imports() -> ImportMap:
    return _model_workflow_imports(
        (
            path
            for path in PACKAGE_ROOT.rglob("*.py")
            if not path.is_relative_to(MODELS_ROOT)
            and not path.is_relative_to(SERVICES_ROOT)
            and "migrations" not in path.parts
        ),
        frozenset(MODEL_WORKFLOW_IMPORT_MODULES),
    )


def _service_video_segment_validation_imports() -> ImportMap:
    return _model_workflow_imports(
        SERVICES_ROOT.rglob("*.py"),
        frozenset({VIDEO_SEGMENT_VALIDATION_MODEL_WORKFLOW_MODULE}),
    )


def _service_frame_annotation_imports() -> ImportMap:
    return _model_workflow_imports(
        SERVICES_ROOT.rglob("*.py"),
        frozenset({FRAME_ANNOTATION_MODEL_WORKFLOW_MODULE}),
    )


def _model_frame_annotation_imports() -> ImportMap:
    return _model_workflow_imports(
        (
            path
            for path in MODELS_ROOT.rglob("*.py")
            if path != MODELS_ROOT / "state" / "frame_annotation.py"
            and path.name != "__init__.py"
            and "migrations" not in path.parts
        ),
        frozenset({FRAME_ANNOTATION_MODEL_WORKFLOW_MODULE}),
    )


def _frame_annotation_segment_identity_imports() -> ImportMap:
    return _model_workflow_imports(
        (
            path
            for path in PACKAGE_ROOT.rglob("*.py")
            if path != MODELS_ROOT / "state" / "frame_annotation_segment_identity.py"
            and path.name != "__init__.py"
            and "migrations" not in path.parts
        ),
        frozenset({FRAME_ANNOTATION_SEGMENT_IDENTITY_MODULE}),
    )


def _model_barrel_imports(paths: Iterable[Path]) -> ImportMap:
    imports: defaultdict[ImportKey, set[str]] = defaultdict(set)

    for path in sorted(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT).as_posix()

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_module = _resolve_import_from_module(path, node)
            else:
                imported_module = ""
            if (
                isinstance(node, ast.ImportFrom)
                and imported_module
                and _is_banned_model_reexport_import(imported_module)
            ):
                imports[(rel_path, imported_module)].update(
                    _imported_name(alias) for alias in node.names
                )
                continue

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_banned_model_reexport_import(alias.name):
                        imports[(rel_path, alias.name)].add(_imported_name(alias))

    return {key: frozenset(names) for key, names in imports.items()}


def _runtime_model_barrel_imports() -> ImportMap:
    return _model_barrel_imports(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if not path.is_relative_to(MODELS_ROOT) and "migrations" not in path.parts
    )


def _clean_model_cohort_barrel_imports() -> ImportMap:
    return _model_barrel_imports(
        (path for root in MODEL_BARREL_CLEAN_ROOTS for path in root.rglob("*.py")),
    ) | _model_barrel_imports(
        MODEL_BARREL_CLEAN_FILES,
    )


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


def _non_schema_pydantic_models() -> set[tuple[str, str]]:
    declarations: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if path.is_relative_to(SCHEMAS_ROOT):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                (isinstance(base, ast.Name) and base.id == "BaseModel")
                or (isinstance(base, ast.Attribute) and base.attr == "BaseModel")
                for base in node.bases
            ):
                declarations.add((relative_path, node.name))
    return declarations


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


def test_relative_imports_resolve_with_python_package_semantics() -> None:
    path = PROJECT_ROOT / "endoreg_db" / "views" / "misc" / "example.py"
    tree = ast.parse("from ...models import VideoFile")
    node = tree.body[0]
    assert isinstance(node, ast.ImportFrom)
    assert _resolve_import_from_module(path, node) == "endoreg_db.models"


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


def test_clean_model_cohorts_do_not_use_model_barrel_imports() -> None:
    assert not _clean_model_cohort_barrel_imports()


def test_runtime_layers_do_not_add_model_workflow_imports() -> None:
    current_imports = _runtime_model_workflow_imports()

    unexpected_import_keys = set(current_imports) - set(
        ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS
    )
    stale_import_keys = set(ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS) - set(
        current_imports
    )
    changed_import_names = {
        key: {
            "unexpected": sorted(
                current_imports[key] - ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS[key]
            ),
            "stale": sorted(
                ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS[key] - current_imports[key]
            ),
        }
        for key in set(current_imports)
        & set(ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS)
        if current_imports[key] != ALLOWLISTED_RUNTIME_MODEL_WORKFLOW_IMPORTS[key]
    }

    assert not unexpected_import_keys, sorted(unexpected_import_keys)
    assert not stale_import_keys, sorted(stale_import_keys)
    assert not changed_import_names, changed_import_names


def test_video_segment_validation_model_has_one_service_ingress() -> None:
    assert (
        _service_video_segment_validation_imports()
        == ALLOWLISTED_SERVICE_VIDEO_SEGMENT_VALIDATION_IMPORTS
    )


def test_frame_annotation_service_ingress_matches_reduction_baseline() -> None:
    assert (
        _service_frame_annotation_imports()
        == ALLOWLISTED_SERVICE_FRAME_ANNOTATION_IMPORTS
    )


def test_frame_annotation_model_consumers_match_reduction_baseline() -> None:
    assert (
        _model_frame_annotation_imports() == ALLOWLISTED_MODEL_FRAME_ANNOTATION_IMPORTS
    )


def test_frame_annotation_segment_identity_consumers_match_baseline() -> None:
    assert (
        _frame_annotation_segment_identity_imports()
        == ALLOWLISTED_FRAME_ANNOTATION_SEGMENT_IDENTITY_IMPORTS
    )


def test_project_code_does_not_reference_model_media_implementation_modules() -> None:
    assert not _project_to_model_implementation_references()


def test_new_local_pydantic_contracts_live_in_schema_package() -> None:
    assert _non_schema_pydantic_models() == ALLOWLISTED_NON_SCHEMA_PYDANTIC_MODELS
