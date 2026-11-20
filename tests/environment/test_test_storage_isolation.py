from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.conf import settings

from endoreg_db.utils.paths import EndoregPathsModel
from tests.runtime_paths import build_test_run_namespace

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
