from __future__ import annotations

from pathlib import Path
from collections.abc import Generator

import pytest

from endoreg_db.config import env as env_module
from endoreg_db.services import environment_readiness as readiness
from endoreg_db.utils import paths as paths_module


@pytest.fixture(autouse=True)
def isolated_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Generator[None, None, None]:
    runtime_root = (tmp_path / "runtime").resolve()
    monkeypatch.setenv(env_module.RUNTIME_ROOT_ENV, str(runtime_root))
    paths_module.clear_runtime_paths_cache()
    yield
    monkeypatch.undo()
    paths_module.clear_runtime_paths_cache()


def _bootstrap_required_directories() -> paths_module.EndoregPathsModel:
    paths = paths_module.get_runtime_paths()
    paths.ensure_directories()

    streamable = paths.storage / "streamable_videos"
    (streamable / "raw").mkdir(parents=True, exist_ok=True)
    (streamable / "processed").mkdir(parents=True, exist_ok=True)

    return paths


def test_check_environment_readiness_accepts_canonical_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _bootstrap_required_directories()

    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    monkeypatch.setenv("MEDIA_URL", "/protected_media/")

    issues = readiness.check_environment_readiness()

    assert not [issue for issue in issues if issue.severity == "critical"]
    assert paths_module.get_runtime_paths().streamable_videos_root == (
        paths.storage / "streamable_videos"
    )


def test_assert_environment_readiness_raises_on_missing_directory() -> None:
    paths = paths_module.get_runtime_paths()

    paths.runtime_root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError) as error:
        readiness.assert_environment_readiness()

    message = str(error.value)
    assert "storage_root_missing" in message


def test_check_environment_readiness_reports_public_media_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap_required_directories()

    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    monkeypatch.setenv("MEDIA_URL", "/media/")

    issues = readiness.check_environment_readiness()

    assert any(issue.code == "media_url_public_mount" for issue in issues)
    assert any(issue.code == "media_url_mismatch" for issue in issues)


def test_protected_media_root_is_derived_from_canonical_storage_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _bootstrap_required_directories()

    # PROTECTED_MEDIA_ROOT is no longer a runtime configuration input.
    monkeypatch.setenv(
        "PROTECTED_MEDIA_ROOT",
        str(paths.runtime_root / "external-media"),
    )

    assert env_module.get_protected_media_root() == paths.storage

    issues = readiness.check_environment_readiness()

    assert not any(issue.code == "protected_media_root_mismatch" for issue in issues)
    assert not any(
        issue.code == "protected_media_root_outside_storage_root" for issue in issues
    )


def test_check_environment_readiness_reports_storage_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _bootstrap_required_directories()
    external_media_root = (tmp_path / "external-media").resolve()
    external_media_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        readiness,
        "get_protected_media_root",
        lambda: external_media_root,
    )

    issues = readiness.check_environment_readiness()

    assert any(issue.code == "protected_media_root_mismatch" for issue in issues)
    assert any(
        issue.code == "protected_media_root_outside_storage_root" for issue in issues
    )

    # The canonical storage tree itself remains unchanged.
    assert paths_module.get_runtime_paths().storage == paths.storage


def test_streamable_roots_are_derived_from_storage_root() -> None:
    paths = _bootstrap_required_directories()

    assert paths.streamable_videos_root == (paths.storage / "streamable_videos")
    assert paths.streamable_videos_raw_media == (paths.streamable_videos_root / "raw")
    assert paths.streamable_videos_processed_media == (
        paths.streamable_videos_root / "processed"
    )


def test_streamable_roots_cannot_be_reconfigured_by_legacy_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _bootstrap_required_directories()
    external = tmp_path / "legacy-streamable"

    monkeypatch.setenv(
        "LX_ANNOTATE_STREAMABLE_VIDEO_ROOT",
        str(external),
    )
    monkeypatch.setenv(
        "LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT",
        str(external / "raw"),
    )
    monkeypatch.setenv(
        "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
        str(external / "processed"),
    )

    assert paths_module.get_runtime_paths().streamable_videos_root == (
        paths.storage / "streamable_videos"
    )
    assert paths_module.get_runtime_paths().streamable_videos_raw_media == (
        paths.storage / "streamable_videos" / "raw"
    )
    assert paths_module.get_runtime_paths().streamable_videos_processed_media == (
        paths.storage / "streamable_videos" / "processed"
    )


def test_check_environment_readiness_reports_missing_streamable_roots() -> None:
    paths = paths_module.get_runtime_paths()
    paths.ensure_directories()
    paths.streamable_videos_raw_media.rmdir()
    paths.streamable_videos_processed_media.rmdir()
    paths.streamable_videos_root.rmdir()

    issues = readiness.check_environment_readiness()

    assert any(issue.code == "streamable_video_root_missing" for issue in issues)
    assert any(issue.code == "streamable_raw_root_missing" for issue in issues)
    assert any(issue.code == "streamable_processed_root_missing" for issue in issues)


def test_assert_environment_readiness_passes_for_complete_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bootstrap_required_directories()

    monkeypatch.setenv("NGINX_PROTECTED_MEDIA_URL", "/protected_media/")
    monkeypatch.setenv("MEDIA_URL", "/protected_media/")

    readiness.assert_environment_readiness()


def test_check_environment_readiness_reports_runtime_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _bootstrap_required_directories()
    escaped = (tmp_path / "outside").resolve()
    escaped.mkdir(parents=True, exist_ok=True)

    replacement = paths.model_copy(update={"watcher_video_drop": escaped})
    monkeypatch.setattr(
        readiness,
        "get_runtime_paths",
        lambda: replacement,
    )

    issues = readiness.check_environment_readiness()

    assert any(
        issue.code == "watcher_video_drop_outside_runtime_root" for issue in issues
    )


def test_check_environment_readiness_reports_streamable_storage_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _bootstrap_required_directories()
    escaped = (tmp_path / "external-streamable").resolve()
    escaped.mkdir(parents=True, exist_ok=True)

    replacement = paths.model_copy(
        update={
            "streamable_videos_root": escaped,
            "streamable_videos_raw_media": escaped / "raw",
            "streamable_videos_processed_media": escaped / "processed",
        }
    )
    monkeypatch.setattr(readiness, "get_runtime_paths", lambda: replacement)

    issues = readiness.check_environment_readiness()

    assert any(
        issue.code == "streamable_video_root_outside_storage_root" for issue in issues
    )
