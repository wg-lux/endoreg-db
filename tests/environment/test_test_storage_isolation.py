from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.conf import settings

from endoreg_db.utils.paths import EndoregPathsModel
from tests.runtime_paths import build_test_run_namespace


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
