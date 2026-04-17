import importlib
import uuid
from pathlib import Path

import pytest

from endoreg_db.config.env import BASE_DIR
from endoreg_db.utils import paths as paths_module


def reload_paths(monkeypatch, **env):
    for key in (
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR",
        "STORAGE_DIR",
        "IO_DIR",
        "PROTECTED_MEDIA_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, str(value))

    return importlib.reload(paths_module)


@pytest.fixture(autouse=True)
def restore_paths_env(monkeypatch):
    yield
    for key in (
        "LX_ANNOTATE_ENCRYPTED_DATA_DIR",
        "STORAGE_DIR",
        "IO_DIR",
        "PROTECTED_MEDIA_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(paths_module)


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

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root_rel,
        STORAGE_DIR=storage_rel,
        IO_DIR=io_rel,
    )

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


def test_storage_tier_helpers_stay_inside_protected_root(monkeypatch, tmp_path):
    protected_root = tmp_path / "protected"

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root,
    )

    manifest_path = reloaded.build_manifest_path(
        command_name="import_sap_ish_zip",
        stem="test_manifest",
    )
    upload_path = reloaded.build_upload_job_relative_path(
        tier="upload_api",
        filename="input.pdf",
        key="abc123",
    )

    assert manifest_path.is_absolute()
    assert manifest_path.is_relative_to(reloaded.PROTECTED_DATA_ROOT)
    assert upload_path.startswith("upload_jobs/api/")
    assert upload_path.endswith("/input.pdf")


def test_paths_module_rejects_storage_dir_outside_protected_root(monkeypatch):
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"
    outside_storage_rel = f"data/tests/runtime/{unique_suffix}/outside/storage"

    with pytest.raises(
        RuntimeError,
        match="STORAGE_DIR must resolve inside LX_ANNOTATE_ENCRYPTED_DATA_DIR",
    ):
        reload_paths(
            monkeypatch,
            LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root_rel,
            STORAGE_DIR=outside_storage_rel,
        )


def test_paths_module_rejects_io_dir_outside_protected_root(monkeypatch):
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"
    outside_io_rel = f"data/tests/runtime/{unique_suffix}/outside/io"

    with pytest.raises(
        RuntimeError,
        match="IO_DIR must resolve inside LX_ANNOTATE_ENCRYPTED_DATA_DIR",
    ):
        reload_paths(
            monkeypatch,
            LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root_rel,
            IO_DIR=outside_io_rel,
        )


def test_protected_media_path_helpers_honor_configured_root(monkeypatch, tmp_path):
    protected_root = tmp_path / "protected"
    protected_media_root = protected_root / "media_mount"
    asset_path = protected_media_root / "streamable_videos" / "raw" / "video.mp4"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"video")

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root,
        PROTECTED_MEDIA_ROOT=protected_media_root,
    )

    assert (
        reloaded.to_protected_media_relative(asset_path)
        == "streamable_videos/raw/video.mp4"
    )
    assert (
        reloaded.resolve_protected_media_path("streamable_videos/raw/video.mp4")
        == asset_path.resolve()
    )


def test_protected_media_relative_path_rejects_unsafe_segments():
    with pytest.raises(ValueError, match="not safe"):
        paths_module.normalize_protected_media_relative_path("../escape.mp4")
