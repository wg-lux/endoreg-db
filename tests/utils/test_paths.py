import importlib
import uuid
from pathlib import Path

from endoreg_db.config.env import BASE_DIR
from endoreg_db.utils import paths as paths_module


def test_data_paths_behaves_like_a_mapping():
    expanded = {**paths_module.data_paths}

    assert expanded["logs"] == paths_module.LOG_DIR
    assert expanded["upload_api"] == paths_module.UPLOAD_API_DIR
    assert expanded["sap_import_drop"] == paths_module.SAP_IMPORT_DROP_DIR
    assert expanded["storage"] == paths_module.STORAGE_DIR
    assert expanded["import_video"] == paths_module.IMPORT_VIDEO_DIR
    assert expanded["import_preanonymized"] == paths_module.IMPORT_PREANONYMIZED_DIR
    assert (
        expanded["import_anonymized_video"] == paths_module.IMPORT_ANONYMIZED_VIDEO_DIR
    )
    assert (
        expanded["import_anonymized_report"]
        == paths_module.IMPORT_ANONYMIZED_REPORT_DIR
    )
    assert expanded["anonym_video"] == paths_module.ANONYM_VIDEO_DIR
    assert expanded["documents"] == paths_module.DOCUMENT_DIR


def test_paths_module_resolves_relative_env_paths(monkeypatch):
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"
    storage_rel = f"{protected_root_rel}/storage"
    io_rel = protected_root_rel

    with monkeypatch.context() as scoped:
        scoped.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", protected_root_rel)
        scoped.setenv("STORAGE_DIR", storage_rel)
        scoped.setenv("IO_DIR", io_rel)

        reloaded = importlib.reload(paths_module)

        expected_root = (BASE_DIR / protected_root_rel).resolve()
        expected_storage = (BASE_DIR / storage_rel).resolve()
        expected_io = (BASE_DIR / io_rel).resolve()

        assert reloaded.PROTECTED_DATA_ROOT == expected_root
        assert reloaded.STORAGE_DIR == expected_storage
        assert reloaded.IO_DIR == expected_io
        assert reloaded.LOG_DIR == expected_io / "logs"
        assert reloaded.UPLOAD_API_DIR == expected_storage / "upload_jobs" / "api"
        assert reloaded.SAP_IMPORT_DROP_DIR == expected_io / "import" / "sap_import"
        assert reloaded.IMPORT_VIDEO_DIR == expected_io / "import" / "video_import"
        assert (
            reloaded.IMPORT_PREANONYMIZED_DIR
            == expected_io / "import" / "preanonymized_import"
        )
        assert (
            reloaded.IMPORT_ANONYMIZED_VIDEO_DIR
            == expected_io / "import" / "anonymized_video_import"
        )
        assert (
            reloaded.IMPORT_ANONYMIZED_REPORT_DIR
            == expected_io / "import" / "anonymized_report_import"
        )
        assert reloaded.ANONYM_VIDEO_DIR == expected_storage / "processed_videos_final"

        for path in (
            reloaded.PROTECTED_DATA_ROOT,
            reloaded.STORAGE_DIR,
            reloaded.IO_DIR,
            reloaded.LOG_DIR,
            reloaded.IMPORT_DIR,
            reloaded.EXPORT_DIR,
            reloaded.IMPORT_VIDEO_DIR,
            reloaded.IMPORT_REPORT_DIR,
            reloaded.IMPORT_PREANONYMIZED_DIR,
            reloaded.IMPORT_ANONYMIZED_VIDEO_DIR,
            reloaded.IMPORT_ANONYMIZED_REPORT_DIR,
            reloaded.ANONYM_VIDEO_DIR,
            reloaded.SENSITIVE_VIDEO_DIR,
        ):
            assert isinstance(path, Path)
            assert path.exists()
            assert path.is_dir()

    importlib.reload(paths_module)


def test_storage_tier_helpers_stay_inside_protected_root():
    manifest_path = paths_module.build_manifest_path(
        command_name="import_sap_ish_zip",
        stem="test_manifest",
    )
    upload_path = paths_module.build_upload_job_relative_path(
        tier="upload_api",
        filename="input.pdf",
        key="abc123",
    )

    assert manifest_path.is_absolute()
    assert manifest_path.is_relative_to(paths_module.PROTECTED_DATA_ROOT)
    assert upload_path.startswith("upload_jobs/api/")


def test_paths_module_rejects_legacy_dirs_outside_protected_root(monkeypatch):
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"
    outside_storage_rel = f"data/tests/runtime/{unique_suffix}/outside/storage"

    with monkeypatch.context() as scoped:
        scoped.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", protected_root_rel)
        scoped.setenv("STORAGE_DIR", outside_storage_rel)

        try:
            importlib.reload(paths_module)
        except RuntimeError as exc:
            assert (
                "STORAGE_DIR must resolve inside LX_ANNOTATE_ENCRYPTED_DATA_DIR"
                in str(exc)
            )
        else:
            raise AssertionError(
                "Expected RuntimeError for STORAGE_DIR outside protected root"
            )

    importlib.reload(paths_module)
