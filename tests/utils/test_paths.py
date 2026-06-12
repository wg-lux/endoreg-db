import importlib
import os
import uuid
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pytest import MonkeyPatch

from endoreg_db.config import env as env_module
from endoreg_db.config.env import BASE_DIR
from endoreg_db.utils import paths as paths_module


def module_path(module: ModuleType, name: str) -> Path:
    value = getattr(module, name)
    if not isinstance(value, Path):
        raise AssertionError(f"{name} is not a Path: {value!r}")
    return value


def module_callable(module: ModuleType, name: str) -> Any:
    return getattr(module, name)


PATH_ENV_KEYS = (
    "LX_ANNOTATE_ENCRYPTED_DATA_DIR",
    "STORAGE_DIR",
    "DATA_DIR",
    "PROTECTED_MEDIA_ROOT",
)


def reload_paths(monkeypatch: MonkeyPatch, **env: Path | str) -> ModuleType:
    for key in PATH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    for key, value in env.items():
        monkeypatch.setenv(key, str(value))

    return importlib.reload(paths_module)


@pytest.fixture(autouse=True)
def restore_paths_env() -> Generator[None, None, None]:
    original_env = {key: os.environ.get(key) for key in PATH_ENV_KEYS}
    yield
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    importlib.reload(paths_module)


def test_data_paths_behaves_like_a_mapping() -> None:
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


def test_legacy_paths_import_reexports_filesystem_paths(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=tmp_path / "protected",
        DATA_DIR=tmp_path / "public",
    )
    legacy_paths = importlib.reload(importlib.import_module("endoreg_db.utils.paths"))

    assert legacy_paths.data_paths is reloaded.data_paths
    assert legacy_paths.LOG_DIR == module_path(reloaded, "LOG_DIR")
    assert legacy_paths.IMPORT_PREANONYMIZED_DIR == module_path(reloaded, "IMPORT_PREANONYMIZED_DIR")
    assert legacy_paths.EndoregPathsModel is reloaded.EndoregPathsModel


def test_paths_module_reexports_env_contracts() -> None:
    assert paths_module.PROTECTED_ROOT_ENV == env_module.PROTECTED_ROOT_ENV
    assert paths_module.STORAGE_DIR_ENV == env_module.STORAGE_DIR_ENV
    assert paths_module.DATA_DIR_ENV == env_module.DATA_DIR_ENV
    assert paths_module.PROTECTED_MEDIA_ROOT_ENV == env_module.PROTECTED_MEDIA_ROOT_ENV
    assert paths_module.DJANGO_SETTINGS_MODULE == env_module.DJANGO_SETTINGS_MODULE


def test_build_protected_runtime_env_normalizes_related_paths(
    tmp_path: Path,
) -> None:
    protected_root = tmp_path / "protected"
    built = env_module.build_protected_runtime_env(
        default_protected_root=protected_root,
        base_dir=tmp_path,
        source={
            "LX_ANNOTATE_ENCRYPTED_DATA_DIR": "protected",
            "STORAGE_DIR": "outside/storage",
            "DATA_DIR": "outside/data",
            "PROTECTED_MEDIA_ROOT": "outside/media",
        },
    )

    assert built["LX_ANNOTATE_ENCRYPTED_DATA_DIR"] == str(protected_root.resolve())
    assert built["STORAGE_DIR"] == str((protected_root / "storage").resolve())
    assert built["DATA_DIR"] == str((tmp_path / "outside" / "data").resolve())
    assert built["PROTECTED_MEDIA_ROOT"] == str((protected_root / "storage").resolve())


def test_paths_module_resolves_relative_env_paths(
    monkeypatch: MonkeyPatch,
) -> None:
    unique_suffix = uuid.uuid4().hex[:8]
    protected_root_rel = f"data/tests/runtime/{unique_suffix}/protected"
    storage_rel = f"{protected_root_rel}/storage"
    data_rel = f"data/tests/runtime/{unique_suffix}/public"

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root_rel,
        STORAGE_DIR=storage_rel,
        DATA_DIR=data_rel,
    )

    expected_root = (BASE_DIR / protected_root_rel).resolve()
    expected_storage = (BASE_DIR / storage_rel).resolve()
    expected_data = (BASE_DIR / data_rel).resolve()

    assert module_path(reloaded, "PROTECTED_DATA_ROOT") == expected_root
    assert module_path(reloaded, "STORAGE_DIR") == expected_storage
    assert module_path(reloaded, "DATA_DIR") == expected_data
    assert module_path(reloaded, "LOG_DIR") == expected_data / "logs"
    assert module_path(reloaded, "QUARANTINE_DIR") == expected_data / "quarantine"
    assert module_path(reloaded, "MIGRATION_STAGING_DIR") == expected_data / "migration_staging"
    assert module_path(reloaded, "UPLOAD_API_DIR") == expected_storage / "upload_jobs" / "api"
    assert module_path(reloaded, "SAP_IMPORT_DROP_DIR") == expected_data / "import" / "sap_import"
    assert module_path(reloaded, "IMPORT_VIDEO_DIR") == expected_data / "import" / "video_import"
    assert (
        module_path(reloaded, "IMPORT_PREANONYMIZED_DIR")
        == expected_data / "import" / "preanonymized_import"
    )
    assert (
        module_path(reloaded, "IMPORT_ANONYMIZED_VIDEO_DIR")
        == expected_data / "import" / "anonymized_video_import"
    )
    assert (
        module_path(reloaded, "IMPORT_ANONYMIZED_REPORT_DIR")
        == expected_data / "import" / "anonymized_report_import"
    )
    assert module_path(reloaded, "EXPORT_DIR") == expected_data / "export"
    assert module_path(reloaded, "SENSITIVE_VIDEO_DIR") == expected_storage / "sensitive_videos"
    assert module_path(reloaded, "SENSITIVE_REPORT_DIR") == expected_storage / "sensitive_reports"
    assert module_path(reloaded, "ANONYM_VIDEO_DIR") == expected_storage / "processed_videos_final"
    assert module_path(reloaded, "ANONYM_REPORT_DIR") == expected_storage / "processed_reports_final"
    assert module_path(reloaded, "RAW_FRAME_DIR") == expected_storage / "raw_frames"
    assert module_path(reloaded, "FRAME_DIR") == expected_storage / "frames"
    assert module_path(reloaded, "WEIGHTS_DIR") == expected_storage / "model_weights"
    assert (
        module_path(reloaded, "MANAGED_ANONYMIZED_VIDEOS_DIR")
        == expected_storage / "processed_videos_final"
    )
    assert (
        module_path(reloaded, "MANAGED_ANONYMIZED_REPORTS_DIR")
        == expected_storage / "processed_reports_final"
    )

    for path in (
        module_path(reloaded, "PROTECTED_DATA_ROOT"),
        module_path(reloaded, "DATA_DIR"),
        module_path(reloaded, "STORAGE_DIR"),
        module_path(reloaded, "LOG_DIR"),
        module_path(reloaded, "IMPORT_DIR"),
        module_path(reloaded, "EXPORT_DIR"),
        module_path(reloaded, "IMPORT_VIDEO_DIR"),
        module_path(reloaded, "IMPORT_REPORT_DIR"),
        module_path(reloaded, "IMPORT_PREANONYMIZED_DIR"),
        module_path(reloaded, "IMPORT_ANONYMIZED_VIDEO_DIR"),
        module_path(reloaded, "IMPORT_ANONYMIZED_REPORT_DIR"),
        module_path(reloaded, "ANONYM_VIDEO_DIR"),
        module_path(reloaded, "SENSITIVE_VIDEO_DIR"),
    ):
        assert isinstance(path, Path)
        assert path.exists()
        assert path.is_dir()


def test_storage_tier_helpers_stay_inside_protected_root(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    protected_root = tmp_path / "protected"
    public_root = tmp_path / "public"

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root,
        DATA_DIR=public_root,
    )

    manifest_path = module_callable(reloaded, "build_manifest_path")(
        command_name="import_sap_ish_zip",
        stem="test_manifest",
    )
    upload_path = module_callable(reloaded, "build_upload_job_relative_path")(
        tier="upload_api",
        filename="input.pdf",
        key="abc123",
    )

    assert manifest_path.is_absolute()
    assert manifest_path.is_relative_to(module_path(reloaded, "DATA_DIR"))
    assert upload_path.startswith("upload_jobs/api/")
    assert upload_path.endswith("/input.pdf")


def test_paths_module_rejects_storage_dir_outside_protected_root(
    monkeypatch: MonkeyPatch,
) -> None:
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


def test_protected_media_path_helpers_honor_configured_root(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
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
        module_callable(reloaded, "to_protected_media_relative")(asset_path)
        == "streamable_videos/raw/video.mp4"
    )
    assert (
        module_callable(reloaded, "resolve_protected_media_path")("streamable_videos/raw/video.mp4")
        == asset_path.resolve()
    )


def test_protected_media_relative_path_rejects_unsafe_segments() -> None:
    with pytest.raises(ValueError, match="not safe"):
        paths_module.normalize_protected_media_relative_path("../escape.mp4")


def test_watcher_intake_dirs_are_distinct_from_protected_media_root(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    protected_root = tmp_path / "protected"
    storage_root = protected_root / "storage"
    data_root = tmp_path / "public"

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root,
        STORAGE_DIR=storage_root,
        DATA_DIR=data_root,
    )

    assert module_callable(reloaded, "protected_media_root")() == storage_root.resolve()
    assert module_path(reloaded, "WATCHER_VIDEO_DROP_DIR").is_relative_to(data_root / "import")
    assert module_path(reloaded, "WATCHER_REPORT_DROP_DIR").is_relative_to(data_root / "import")
    assert module_path(reloaded, "WATCHER_PREANONYMIZED_DROP_DIR").is_relative_to(data_root / "import")
    assert not module_path(reloaded, "WATCHER_VIDEO_DROP_DIR").is_relative_to(
        module_callable(reloaded, "protected_media_root")()
    )
    assert not module_path(reloaded, "WATCHER_REPORT_DROP_DIR").is_relative_to(
        module_callable(reloaded, "protected_media_root")()
    )
    assert not module_path(reloaded, "WATCHER_PREANONYMIZED_DROP_DIR").is_relative_to(
        module_callable(reloaded, "protected_media_root")()
    )


def test_resolve_existing_protected_media_path_rejects_intake_and_accepts_managed_files(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    protected_root = tmp_path / "protected"
    storage_root = protected_root / "storage"
    data_root = tmp_path / "public"

    reloaded = reload_paths(
        monkeypatch,
        LX_ANNOTATE_ENCRYPTED_DATA_DIR=protected_root,
        STORAGE_DIR=storage_root,
        DATA_DIR=data_root,
    )

    intake_file = module_path(reloaded, "WATCHER_REPORT_DROP_DIR") / "incoming.pdf"
    intake_file.parent.mkdir(parents=True, exist_ok=True)
    intake_file.write_bytes(b"%PDF-1.4 intake")

    managed_file = module_path(reloaded, "UPLOAD_WATCHER_DIR") / "job-123" / "incoming.pdf"
    managed_file.parent.mkdir(parents=True, exist_ok=True)
    managed_file.write_bytes(b"%PDF-1.4 managed")

    assert module_callable(reloaded, "resolve_existing_protected_media_path")(intake_file) is None
    assert (
        module_callable(reloaded, "resolve_existing_protected_media_path")(managed_file)
        == managed_file.resolve()
    )
    assert (
        module_callable(reloaded, "resolve_existing_protected_media_path")(
            "upload_jobs/watcher/job-123/incoming.pdf"
        )
        == managed_file.resolve()
    )
