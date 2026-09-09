from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_route_matrix import (
    RouteMatrixError,
    find_missing_backend_routes,
    normalize_route_path,
    parse_backend_routes,
    parse_documented_routes,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_route_matrix.py"


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def test_normalize_route_path_removes_converter_types() -> None:
    assert normalize_route_path("/endoreg-api/upload/<uuid:id>/status/") == (
        "/endoreg-api/upload/<id>/status/"
    )
    assert normalize_route_path("/endoreg-api/media/<pk>/") == (
        "/endoreg-api/media/<pk>/"
    )


def test_markdown_contract_requires_existing_test_reference(tmp_path: Path) -> None:
    source = (
        "| Route | Purpose | Test Coverage |\n"
        "|---|---|---|\n"
        "| `/endoreg-api/upload/` | Upload | `tests/missing.py` |\n"
    )

    with pytest.raises(RouteMatrixError, match="references missing tests"):
        parse_documented_routes(source, project_root=tmp_path)


def test_documented_routes_compare_against_normalized_backend(tmp_path: Path) -> None:
    test_path = tmp_path / "tests" / "test_upload.py"
    test_path.parent.mkdir()
    test_path.write_text("", encoding="utf-8")
    source = (
        "| Route | Purpose | Test Coverage |\n"
        "|---|---|---|\n"
        "| `/endoreg-api/upload/<id>/status/` | Status | "
        "`tests/test_upload.py` |\n"
    )

    documented = parse_documented_routes(source, project_root=tmp_path)
    backend = parse_backend_routes("/endoreg-api/upload/<uuid:id>/status/,view,name,\n")

    assert find_missing_backend_routes(documented, backend) == ()


def test_cli_fails_when_versioned_matrix_is_missing(tmp_path: Path) -> None:
    result = _run_checker("--matrix", str(tmp_path / "missing.md"))

    assert result.returncode == 2
    assert "matrix file not found" in result.stderr
    assert "skipped" not in result.stderr


def test_cli_fails_on_documented_backend_drift(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.md"
    matrix.write_text(
        "| Route | Purpose | Test Coverage |\n"
        "|---|---|---|\n"
        "| `/endoreg-api/missing/` | Missing route | `tests/test_resolve.py` |\n",
        encoding="utf-8",
    )
    urls_csv = tmp_path / "urls.csv"
    urls_csv.write_text("/endoreg-api/present/,view,name,\n", encoding="utf-8")

    result = _run_checker(
        "--matrix",
        str(matrix),
        "--urls-csv",
        str(urls_csv),
        "--warn-on-missing-backend",
    )

    assert result.returncode == 1
    assert "documented backend route is missing" in result.stderr


@pytest.mark.parametrize("effective_missing, expected_code", [(0, 0), (1, 1)])
def test_cli_retains_legacy_json_contract(
    tmp_path: Path,
    effective_missing: int,
    expected_code: int,
) -> None:
    matrix = tmp_path / "matrix.json"
    matrix.write_text(
        json.dumps(
            {
                "effective_missing_backend_count": effective_missing,
                "missing_backend_count": effective_missing,
                "frontend_checked": 4,
                "results": [{"key": "legacy", "status": "missing_backend"}],
                "effective_missing_backend": (
                    [{"key": "legacy"}] if effective_missing else []
                ),
            }
        ),
        encoding="utf-8",
    )

    result = _run_checker(
        "--matrix",
        str(matrix),
        "--warn-on-missing-backend",
    )

    assert result.returncode == expected_code
    assert "legacy-json frontend_checked=4" in result.stdout
