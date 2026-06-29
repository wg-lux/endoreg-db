from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from endoreg_db.services import environment_readiness as readiness


def test_path_within_rejects_external_candidate() -> None:
    assert not readiness._path_within(Path("/tmp/root"), Path("/tmp/other/file"))
    assert readiness._path_within(Path("/tmp/root"), Path("/tmp/root/sub/file"))


def test_check_directory_access_reports_missing_and_not_dir() -> None:
    issues_missing = readiness._check_directory_access(
        Path("/this/path/does/not/exist"), code_prefix="data_root"
    )
    assert issues_missing[0].code == "data_root_missing"
    assert issues_missing[0].severity == "critical"

    file_path = Path("/tmp")
    # Force "not directory" branch without touching filesystem
    with (
        patch(
            "endoreg_db.services.environment_readiness.os.path.exists",
            return_value=True,
        ),
        patch(
            "endoreg_db.services.environment_readiness.os.path.isdir",
            return_value=False,
        ),
    ):
        issues_not_dir = readiness._check_directory_access(
            file_path, code_prefix="data_root"
        )
    assert issues_not_dir[0].code == "data_root_not_dir"


def test_check_directory_access_reports_permission_denied() -> None:
    with (
        patch(
            "endoreg_db.services.environment_readiness.os.path.exists",
            return_value=True,
        ),
        patch(
            "endoreg_db.services.environment_readiness.os.path.isdir", return_value=True
        ),
        patch(
            "endoreg_db.services.environment_readiness.os.access", return_value=False
        ),
    ):
        issues = readiness._check_directory_access(
            Path("/tmp/root"), code_prefix="data_root"
        )
    assert {issue.code for issue in issues} == {
        "data_root_read_denied",
        "data_root_write_denied",
        "data_root_execute_denied",
    }


def test_check_protected_media_contract_collects_all_critical_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readiness,
        "get_media_url",
        lambda: "/media/",
    )
    monkeypatch.setattr(
        readiness,
        "get_protected_media_url",
        lambda: "/other/",
    )
    monkeypatch.setattr(
        readiness, "get_protected_media_root", lambda: Path("/tmp/outside")
    )
    issues = readiness._check_protected_media_contract()
    codes = {issue.code for issue in issues}
    assert "protected_media_url_invalid" in codes
    assert "media_url_public_mount" in codes
    assert "media_url_mismatch" in codes
    assert "protected_media_root_outside_protected_root" in codes
