from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from endoreg_db.config import env as env_module
from endoreg_db.utils import paths as paths_module


@pytest.fixture(autouse=True)
def isolate_runtime_root(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> Generator[None, None, None]:
    """Give every test an isolated canonical runtime root."""

    runtime_root = (tmp_path / "runtime-root").resolve()
    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, str(runtime_root))
    paths_module.clear_runtime_paths_cache()

    yield

    monkeypatch.undo()
    paths_module.clear_runtime_paths_cache()


def test_runtime_root_is_the_only_path_configuration_input(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = (tmp_path / "configured-runtime").resolve()
    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, str(runtime_root))
    paths_module.clear_runtime_paths_cache()

    paths = paths_module.get_runtime_paths()

    assert paths.runtime_root == runtime_root
    assert paths.storage == runtime_root / "storage"
    assert paths.terminology == runtime_root / "terminology"
    assert paths.import_dir == runtime_root / "import"
    assert paths.export_dir == runtime_root / "export"
    assert paths.logs == runtime_root / "logs"
    assert paths.quarantine == runtime_root / "quarantine"
    assert paths.migration_staging == runtime_root / "migration_staging"


def test_runtime_root_must_be_absolute(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, "relative/runtime")
    paths_module.clear_runtime_paths_cache()

    with pytest.raises(
        env_module.EnvironmentValueError,
        match="LX_RUNTIME_ROOT must be an absolute filesystem path",
    ):
        paths_module.get_runtime_paths()


def test_path_model_construction_is_pure(tmp_path: Path) -> None:
    runtime_root = tmp_path / "pure-runtime"

    paths = paths_module.EndoregPathsModel.from_root(runtime_root)

    assert paths.runtime_root == runtime_root.resolve()
    assert not runtime_root.exists()
    assert not paths.storage.exists()
    assert not paths.import_dir.exists()


def test_ensure_directories_bootstraps_resolved_topology(tmp_path: Path) -> None:
    paths = paths_module.EndoregPathsModel.from_root(tmp_path / "runtime")

    paths.ensure_directories()

    for path in paths.dirs:
        assert path.exists()
        assert path.is_dir()


def test_get_runtime_paths_is_cached() -> None:
    first = paths_module.get_runtime_paths()
    second = paths_module.get_runtime_paths()

    assert first is second


def test_clear_runtime_paths_cache_re_resolves_configuration(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_root = (tmp_path / "first").resolve()
    second_root = (tmp_path / "second").resolve()

    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, str(first_root))
    paths_module.clear_runtime_paths_cache()
    first = paths_module.get_runtime_paths()

    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, str(second_root))
    assert paths_module.get_runtime_paths() is first

    paths_module.clear_runtime_paths_cache()
    second = paths_module.get_runtime_paths()

    assert second is not first
    assert second.runtime_root == second_root


def test_storage_and_operational_paths_have_expected_topology() -> None:
    paths = paths_module.get_runtime_paths()

    assert paths.documents == paths.storage / "documents"
    assert paths.transcoding == paths.storage / "temp"
    assert paths.sensitive_video == paths.storage / "sensitive_videos"
    assert paths.sensitive_report == paths.storage / "sensitive_reports"
    assert paths.anonym_video == paths.storage / "processed_videos_final"
    assert paths.anonym_report == paths.storage / "processed_reports_final"
    assert paths.raw_frame == paths.storage / "raw_frames"
    assert paths.frame == paths.storage / "frames"
    assert paths.weights == paths.storage / "model_weights"
    assert paths.managed_sensitive_sidecars == paths.storage / "sensitive_sidecars"

    assert paths.import_video == paths.import_dir / "video_import"
    assert paths.import_report == paths.import_dir / "report_import"
    assert paths.import_preanonymized == paths.import_dir / "preanonymized_import"
    assert paths.import_anonymized_video == paths.import_dir / "anonymized_video_import"
    assert (
        paths.import_anonymized_report == paths.import_dir / "anonymized_report_import"
    )
    assert paths.video_export == paths.export_dir / "video_export"
    assert paths.report_export == paths.export_dir / "report_export"


def test_storage_tier_matrix_resolves_typed_model_fields() -> None:
    paths = paths_module.get_runtime_paths()

    for tier in paths_module.StorageTier:
        field_name = paths_module.STORAGE_TIER_FIELDS[tier]

        assert field_name in paths_module.EndoregPathsModel.model_fields

        root = paths_module.get_storage_tier_root(tier)
        assert root == getattr(paths, field_name)

        if tier in paths_module.STORAGE_TIERS:
            assert root.is_relative_to(paths.storage)
        else:
            assert root.is_relative_to(paths.runtime_root)


def test_storage_tier_helpers_respect_security_boundaries() -> None:
    paths = paths_module.get_runtime_paths()

    upload_path = paths_module.resolve_storage_tier_path(
        paths_module.StorageTier.UPLOAD_API,
        "ab",
        "abc123",
        "input.pdf",
    )
    manifest_path = paths_module.build_manifest_path(
        command_name="import_sap_ish_zip",
        stem="test_manifest",
    )

    assert upload_path.is_relative_to(paths.storage)
    assert manifest_path.is_relative_to(paths.runtime_root)
    assert not manifest_path.is_relative_to(paths.storage)


def test_build_upload_job_relative_path_returns_storage_relative_name() -> None:
    relative = paths_module.build_upload_job_relative_path(
        tier=paths_module.StorageTier.UPLOAD_API,
        filename="../unsafe/input.pdf",
        key="abc123",
    )

    assert relative.startswith("upload_jobs/api/")
    assert relative.endswith("/input.pdf")
    assert ".." not in Path(relative).parts


@pytest.mark.parametrize(
    "tier",
    [
        paths_module.StorageTier.MANIFEST,
        paths_module.StorageTier.QUARANTINE,
        paths_module.StorageTier.WATCHER_VIDEO_DROP,
    ],
)
def test_build_upload_job_relative_path_rejects_non_storage_tiers(
    tier: paths_module.StorageTier,
) -> None:
    with pytest.raises(ValueError, match="storage-backed"):
        paths_module.build_upload_job_relative_path(
            tier=tier,
            filename="input.pdf",
            key="abc123",
        )


def test_ensure_within_runtime_root_accepts_runtime_paths_and_rejects_escape(
    tmp_path: Path,
) -> None:
    paths = paths_module.get_runtime_paths()
    inside = paths.runtime_root / "import" / "file.txt"
    outside = tmp_path / "outside.txt"

    assert paths_module.ensure_within_runtime_root(inside) == inside.resolve()

    with pytest.raises(ValueError, match="outside runtime root"):
        paths_module.ensure_within_runtime_root(outside)


def test_ensure_within_storage_root_accepts_storage_paths_and_rejects_runtime_siblings(
    tmp_path: Path,
) -> None:
    paths = paths_module.get_runtime_paths()
    inside = paths.storage / "documents" / "file.pdf"
    runtime_sibling = paths.import_dir / "file.pdf"

    assert paths_module.ensure_within_storage_root(inside) == inside.resolve()

    with pytest.raises(ValueError, match="outside storage root"):
        paths_module.ensure_within_storage_root(runtime_sibling)


def test_protected_media_root_is_canonical_storage_root() -> None:
    paths = paths_module.get_runtime_paths()

    assert paths_module.protected_media_root() == paths.storage


def test_protected_media_relative_path_helpers_round_trip(tmp_path: Path) -> None:
    paths = paths_module.get_runtime_paths()
    media_file = paths.storage / "streamable_videos" / "raw" / "video.mp4"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"video")

    relative = paths_module.to_protected_media_relative(media_file)

    assert relative == "streamable_videos/raw/video.mp4"
    assert paths_module.resolve_protected_media_path(relative) == media_file.resolve()
    assert (
        paths_module.resolve_existing_protected_media_path(relative)
        == media_file.resolve()
    )


def test_resolve_existing_protected_media_path_rejects_runtime_non_storage_file() -> (
    None
):
    paths = paths_module.get_runtime_paths()

    intake_file = paths.watcher_report_drop / "incoming.pdf"
    intake_file.parent.mkdir(parents=True, exist_ok=True)
    intake_file.write_bytes(b"%PDF-1.4")

    assert paths_module.resolve_existing_protected_media_path(intake_file) is None


def test_resolve_existing_protected_media_path_accepts_managed_storage_file() -> None:
    paths = paths_module.get_runtime_paths()

    managed_file = paths.upload_watcher / "job-123" / "incoming.pdf"
    managed_file.parent.mkdir(parents=True, exist_ok=True)
    managed_file.write_bytes(b"%PDF-1.4 managed")

    assert (
        paths_module.resolve_existing_protected_media_path(managed_file)
        == managed_file.resolve()
    )
    assert (
        paths_module.resolve_existing_protected_media_path(
            "upload_jobs/watcher/job-123/incoming.pdf"
        )
        == managed_file.resolve()
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "../escape.mp4",
        "streamable_videos/../../etc/passwd",
        "streamable_videos/../processed/video.mp4",
        "/etc/passwd",
        "",
    ],
)
def test_resolve_protected_media_path_rejects_unsafe_input(
    relative_path: str,
) -> None:
    with pytest.raises(ValueError):
        paths_module.resolve_protected_media_path(relative_path)


def test_to_storage_relative_is_strict() -> None:
    paths = paths_module.get_runtime_paths()

    storage_file = paths.storage / "documents" / "report.pdf"
    runtime_file = paths.import_dir / "report.pdf"

    assert paths_module.to_storage_relative(storage_file) == "documents/report.pdf"

    with pytest.raises(ValueError, match="outside storage root"):
        paths_module.to_storage_relative(runtime_file)


def test_runtime_relative_helpers_round_trip() -> None:
    paths = paths_module.get_runtime_paths()
    runtime_path = paths.import_dir / "video_import" / "incoming.mp4"

    relative = paths_module.to_runtime_relative(runtime_path)

    assert relative == "import/video_import/incoming.mp4"
    assert paths_module.resolve_runtime_path(relative) == runtime_path.resolve()


@pytest.mark.parametrize(
    "relative_path",
    [
        "../escape",
        "import/../../escape",
        "/etc/passwd",
        "",
    ],
)
def test_resolve_runtime_path_rejects_unsafe_input(relative_path: str) -> None:
    with pytest.raises(ValueError):
        paths_module.resolve_runtime_path(relative_path)


def test_resolve_protected_runtime_path_accepts_absolute_and_relative_paths() -> None:
    paths = paths_module.get_runtime_paths()
    fallback = paths.transcoding / "fallback"
    absolute = paths.storage / "temp" / "absolute"
    relative = Path("storage/temp/relative")

    assert (
        paths_module.resolve_protected_runtime_path(
            None,
            fallback=fallback,
        )
        == fallback.resolve()
    )
    assert (
        paths_module.resolve_protected_runtime_path(
            absolute,
            fallback=fallback,
        )
        == absolute.resolve()
    )
    assert (
        paths_module.resolve_protected_runtime_path(
            relative,
            fallback=fallback,
        )
        == (paths.runtime_root / relative).resolve()
    )


def test_resolve_protected_runtime_path_rejects_escape(
    tmp_path: Path,
) -> None:
    paths = paths_module.get_runtime_paths()
    fallback = paths.transcoding / "fallback"

    with pytest.raises(ValueError, match="outside runtime root"):
        paths_module.resolve_protected_runtime_path(
            tmp_path / "outside",
            fallback=fallback,
        )


def test_path_read_helpers_do_not_bootstrap_directories(
    monkeypatch: MonkeyPatch,
) -> None:
    paths = paths_module.get_runtime_paths()
    media_file = paths.storage / "streamable_videos" / "processed" / "video.mp4"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"managed media")

    def fail_if_directory_bootstrap_runs(path: Path) -> Path:
        raise AssertionError(f"read-only path helper tried to ensure {path}")

    monkeypatch.setattr(
        paths_module,
        "_ensure_directory",
        fail_if_directory_bootstrap_runs,
    )

    assert paths_module.protected_media_root() == paths.storage
    assert (
        paths_module.resolve_existing_protected_media_path(
            "streamable_videos/processed/video.mp4"
        )
        == media_file.resolve()
    )


def test_validate_runtime_storage_contract_accepts_bootstrapped_tree() -> None:
    paths = paths_module.get_runtime_paths()
    paths.ensure_directories()

    paths_module.validate_runtime_storage_contract()


def test_validate_runtime_storage_contract_rejects_missing_tree() -> None:
    paths = paths_module.get_runtime_paths()

    assert not paths.runtime_root.exists()

    with pytest.raises(RuntimeError, match="Runtime path does not exist"):
        paths_module.validate_runtime_storage_contract()


def test_validate_runtime_storage_contract_rejects_non_storage_upload_root(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = paths_module.get_runtime_paths()
    paths.ensure_directories()

    invalid_root = (tmp_path / "outside-upload-root").resolve()
    replacement = paths.model_copy(update={"upload_api": invalid_root})

    monkeypatch.setattr(
        paths_module,
        "get_runtime_paths",
        lambda: replacement,
    )

    with pytest.raises(RuntimeError, match="Storage path contract invalid"):
        paths_module.validate_runtime_storage_contract()


def test_paths_module_does_not_export_removed_legacy_aliases() -> None:
    removed_names = {
        "data_paths",
        "data_paths_model",
        "DATA_DIR",
        "PROTECTED_DATA_ROOT",
        "IMPORT_DIR",
        "TRANSCODING_DIR",
        "WEIGHTS_DIR",
        "ANONYM_VIDEO_DIR",
        "SENSITIVE_VIDEO_DIR",
        "LOG_DIR",
        "PROTECTED_ROOT_ENV",
        "DATA_DIR_ENV",
        "STORAGE_DR_ENV",
        "PROTECTED_MEDIA_ROOT_ENV",
    }

    for name in removed_names:
        assert not hasattr(paths_module, name)


@pytest.mark.parametrize(
    "legacy_variable",
    ["DATA_DIR", "LX_ANNOTATE_DATA_DIR", "PROTECTED_MEDIA_ROOT", "TRANSCODING_DIR"],
)
def test_legacy_environment_cannot_override_central_paths(
    monkeypatch: MonkeyPatch, tmp_path: Path, legacy_variable: str
) -> None:
    expected = paths_module.get_runtime_paths()
    monkeypatch.setenv(legacy_variable, str(tmp_path / "forbidden-override"))
    paths_module.clear_runtime_paths_cache()

    actual = paths_module.get_runtime_paths()
    for field_name in paths_module.EndoregPathsModel.model_fields:
        expected_path = getattr(expected, field_name)
        if isinstance(expected_path, Path):
            assert getattr(actual, field_name) == expected_path
    assert actual.dirs == expected.dirs


def test_env_module_does_not_reintroduce_legacy_runtime_path_contract() -> None:
    removed_names = {
        "DATA_DIR_ENV",
        "PROTECTED_ROOT_ENV",
        "PROTECTED_MEDIA_ROOT_ENV",
        "build_protected_runtime_env",
        "get_data_dir",
        "TEST_DATA_ROOT",
        "TEST_PROTECTED_ROOT",
    }

    for name in removed_names:
        assert not hasattr(env_module, name)


def test_runtime_root_environment_is_not_rewritten(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_root = (tmp_path / "runtime").resolve()
    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, str(runtime_root))
    before = dict(os.environ)

    paths_module.clear_runtime_paths_cache()
    paths_module.get_runtime_paths()

    for key in (
        "DATA_DIR",
        "LX_ANNOTATE_DATA_DIR",
        "PROTECTED_MEDIA_ROOT",
    ):
        assert os.environ.get(key) == before.get(key)
