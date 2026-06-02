import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy_module", "target_module", "export_name"),
    [
        (
            "endoreg_db.utils.cors",
            "endoreg_db.utils.web.cors",
            "resolve_response_origin",
        ),
        (
            "endoreg_db.utils.dataloader",
            "endoreg_db.utils.data_loading.dataloader",
            "load_model_data_from_yaml",
        ),
        (
            "endoreg_db.utils.dates",
            "endoreg_db.utils.core.dates",
            "ensure_aware_datetime",
        ),
        (
            "endoreg_db.utils.django_static",
            "endoreg_db.utils.web.django_static",
            "static",
        ),
        ("endoreg_db.utils.env", "endoreg_db.utils.core.env", "get_env_var"),
        (
            "endoreg_db.utils.file_operations",
            "endoreg_db.utils.filesystem.file_operations",
            "atomic_write_file",
        ),
        ("endoreg_db.utils.hashs", "endoreg_db.utils.security.hashs", "get_pdf_hash"),
        (
            "endoreg_db.utils.media_urls",
            "endoreg_db.utils.web.media_urls",
            "build_pdf_stream_path",
        ),
        ("endoreg_db.utils.names", "endoreg_db.utils.core.names", "guess_name_gender"),
        (
            "endoreg_db.utils.operation_log",
            "endoreg_db.utils.observability.operation_log",
            "record_operation",
        ),
        (
            "endoreg_db.utils.parse_and_generate_yaml",
            "endoreg_db.utils.data_loading.parse_and_generate_yaml",
            "collect_center_names",
        ),
        (
            "endoreg_db.utils.permissions",
            "endoreg_db.utils.web.permissions",
            "EnvironmentAwarePermission",
        ),
        (
            "endoreg_db.utils.requirement_helpers",
            "endoreg_db.utils.data_loading.requirement_helpers",
            None,
        ),
        (
            "endoreg_db.utils.rust_backend",
            "endoreg_db.utils.system.rust_backend",
            "sha256_file_hex",
        ),
        (
            "endoreg_db.utils.setup_config",
            "endoreg_db.utils.data_loading.setup_config",
            "SetupConfig",
        ),
        (
            "endoreg_db.utils.storage_profile",
            "endoreg_db.utils.storage.profile",
            "PayloadKind",
        ),
        (
            "endoreg_db.utils.storage_streaming",
            "endoreg_db.utils.storage.streaming",
            "iter_field_file_bytes",
        ),
        (
            "endoreg_db.utils.structured_logging",
            "endoreg_db.utils.observability.structured_logging",
            "emit_structured_event",
        ),
        (
            "endoreg_db.utils.translation",
            "endoreg_db.utils.core.translation",
            "build_multilingual_response",
        ),
        ("endoreg_db.utils.uuid", "endoreg_db.utils.core.uuid", "get_uuid"),
        (
            "endoreg_db.utils.validate_endo_roi",
            "endoreg_db.utils.validation.endo_roi",
            "validate_endo_roi",
        ),
        (
            "endoreg_db.utils.yaml_model_loader",
            "endoreg_db.utils.data_loading.yaml_model_loader",
            "load_model_data_from_yaml",
        ),
    ],
)
def test_legacy_utils_modules_reexport_moved_module_imports(
    legacy_module, target_module, export_name
):
    legacy = importlib.import_module(legacy_module)
    target = importlib.import_module(target_module)

    assert "__getattr__" not in legacy.__dict__
    assert "_compat_target" not in legacy.__dict__

    if export_name is None:
        assert legacy.__all__ == []
        return

    assert getattr(legacy, export_name) is getattr(target, export_name)
