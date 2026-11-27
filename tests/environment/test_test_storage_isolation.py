from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.conf import settings

from endoreg_db.utils.paths import EndoregPathsModel
from tests.runtime_paths import (
    build_test_run_namespace,
    find_stale_run_roots,
    parse_test_run_namespace_pid,
)

pytestmark = pytest.mark.no_db


def test_pure_test_does_not_allocate_database_or_temporary_directory(
    request: pytest.FixtureRequest,
) -> None:
    assert "db" not in request.fixturenames
    assert "django_db_setup" not in request.fixturenames
    assert "tmp_path" not in request.fixturenames


@pytest.mark.parametrize(
    ("worker_id", "process_id", "expected"),
    [
        (None, 1234, "main-1234"),
        ("gw0", 1234, "gw0-1234"),
        ("gw3", 9876, "gw3-9876"),
    ],
)
def test_test_run_namespace_isolates_non_xdist_processes(
    worker_id: str | None,
    process_id: int,
    expected: str,
) -> None:
    assert build_test_run_namespace(worker_id, process_id) == expected


def test_test_run_namespace_rejects_invalid_process_id() -> None:
    with pytest.raises(ValueError, match="process_id must be positive"):
        build_test_run_namespace(None, 0)


@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        ("main-1234", 1234),
        ("gw0-42", 42),
        ("main", None),
        ("gw0-abc", None),
        ("gw0-0", None),
        ("", None),
    ],
)
def test_parse_test_run_namespace_pid(namespace: str, expected: int | None) -> None:
    assert parse_test_run_namespace_pid(namespace) == expected


def test_find_stale_run_roots_only_returns_dead_namespaced_directories(
    tmp_path: Path,
) -> None:
    for name in ("main-10", "gw0-11", "gw1-12", "unrelated"):
        (tmp_path / name).mkdir()
    (tmp_path / "main-13").write_text("not a directory")
    alive = {11}

    stale = find_stale_run_roots(tmp_path, is_alive=lambda pid: pid in alive)

    assert [path.name for path in stale] == ["gw1-12", "main-10"]


def test_find_stale_run_roots_handles_missing_root(tmp_path: Path) -> None:
    assert find_stale_run_roots(tmp_path / "missing") == []


def test_current_run_root_is_never_stale() -> None:
    namespace = os.environ["ENDOREG_TEST_RUN_NAMESPACE"]
    process_id = parse_test_run_namespace_pid(namespace)
    assert process_id == os.getpid()


def test_video_and_pdf_storage_share_the_session_root() -> None:
    paths = EndoregPathsModel.from_environment()
    storage_root = Path(paths.storage).resolve()

    assert os.environ["ENDOREG_TEST_RUN_NAMESPACE"] in storage_root.parts
    assert Path(settings.MEDIA_ROOT).resolve() == storage_root

    for media_path in (
        paths.sensitive_video,
        paths.anonym_video,
        paths.sensitive_report,
        paths.anonym_report,
    ):
        media_path.resolve().relative_to(storage_root)


def test_unit_video_test_replaces_real_anonymizer() -> None:
    from endoreg_db.import_files import video_import_service

    anonymizer = video_import_service.VideoAnonymizer
    assert anonymizer is not None
    assert anonymizer.__name__ == "DummyVideoAnonymizer"


@pytest.mark.integration
def test_video_integration_keeps_real_anonymizer_without_temporary_directory(
    request: pytest.FixtureRequest,
) -> None:
    from endoreg_db.import_files import video_import_service

    # None means the real class is still loaded lazily on first use.
    anonymizer = video_import_service.VideoAnonymizer
    assert anonymizer is None or anonymizer.__name__ != "DummyVideoAnonymizer"
    assert "tmp_path" not in request.fixturenames
