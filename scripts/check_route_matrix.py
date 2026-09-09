#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from io import StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import cast


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "docs" / "api_route_test_matrix.md"
REQUIRED_LEGACY_KEYS = {
    "effective_missing_backend_count",
    "missing_backend_count",
    "frontend_checked",
    "results",
}
ROUTE_PARAMETER_PATTERN = re.compile(r"<(?:(?:[^:<>]+):)?([^<>]+)>")
TEST_REFERENCE_PATTERN = re.compile(
    r"`(?P<path>tests/[A-Za-z0-9_./-]+\.py)(?:::[^`]*)?`"
)


class RouteMatrixError(ValueError):
    """The route matrix cannot be used as a reproducible contract."""


@dataclass(frozen=True, slots=True)
class DocumentedRoute:
    path: str
    purpose: str
    test_paths: tuple[str, ...]
    line_number: int


@dataclass(frozen=True, slots=True)
class LegacyRouteMatrix:
    effective_missing_backend_count: int
    missing_backend_count: int
    frontend_checked: int
    missing_backend_keys: tuple[str, ...]
    effective_missing_backend_keys: tuple[str, ...]


def normalize_route_path(path: str) -> str:
    """Normalize Django converter syntax while retaining parameter identity."""

    return ROUTE_PARAMETER_PATTERN.sub(r"<\1>", path.strip())


def parse_documented_routes(
    source: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> tuple[DocumentedRoute, ...]:
    """Parse canonical route rows and verify their referenced test files."""

    routes: list[DocumentedRoute] = []
    seen_paths: set[str] = set()
    for line_number, line in enumerate(source.splitlines(), start=1):
        if not line.startswith("|"):
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split("|"))
        if len(cells) != 3:
            continue
        route_cell, purpose, coverage_cell = cells
        if not (route_cell.startswith("`") and route_cell.endswith("`")):
            continue
        route = route_cell[1:-1]
        if not route.startswith("/endoreg-api/"):
            continue
        normalized_route = normalize_route_path(route)
        if normalized_route in seen_paths:
            raise RouteMatrixError(
                f"duplicate documented route at line {line_number}: {route}"
            )
        if not purpose:
            raise RouteMatrixError(
                f"documented route at line {line_number} has no purpose: {route}"
            )
        test_paths = tuple(
            match.group("path")
            for match in TEST_REFERENCE_PATTERN.finditer(coverage_cell)
        )
        if not test_paths:
            raise RouteMatrixError(
                f"documented route at line {line_number} has no test reference: {route}"
            )
        missing_tests = tuple(
            test_path
            for test_path in test_paths
            if not (project_root / test_path).is_file()
        )
        if missing_tests:
            raise RouteMatrixError(
                f"documented route at line {line_number} references missing tests: "
                f"{', '.join(missing_tests)}"
            )
        seen_paths.add(normalized_route)
        routes.append(
            DocumentedRoute(
                path=normalized_route,
                purpose=purpose,
                test_paths=test_paths,
                line_number=line_number,
            )
        )
    if not routes:
        raise RouteMatrixError("route matrix contains no canonical route contracts")
    return tuple(routes)


def parse_backend_routes(csv_source: str) -> frozenset[str]:
    """Read normalized route paths from django-extensions show_urls CSV."""

    routes = {
        normalize_route_path(row[0])
        for row in csv.reader(StringIO(csv_source))
        if row and row[0].strip()
    }
    if not routes:
        raise RouteMatrixError("backend URL inventory is empty")
    return frozenset(routes)


def export_backend_routes() -> str:
    """Export the real Django URL inventory in an isolated subprocess."""

    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "endoreg_db.config.settings.test"
    result = subprocess.run(
        [sys.executable, "manage.py", "show_urls", "--format", "csv"],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "show_urls returned no diagnostic"
        raise RouteMatrixError(
            f"backend URL export failed with exit code {result.returncode}: {detail}"
        )
    return result.stdout


def find_missing_backend_routes(
    documented_routes: tuple[DocumentedRoute, ...],
    backend_routes: frozenset[str],
) -> tuple[DocumentedRoute, ...]:
    return tuple(
        route for route in documented_routes if route.path not in backend_routes
    )


def _required_legacy_integer(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RouteMatrixError(f"legacy route matrix '{key}' must be an integer")
    if value < 0:
        raise RouteMatrixError(f"legacy route matrix '{key}' must be non-negative")
    return value


def _legacy_keys(rows: object, *, status: str | None = None) -> tuple[str, ...]:
    if not isinstance(rows, list):
        raise RouteMatrixError("legacy route matrix rows must be a list")
    keys: list[str] = []
    for row_value in rows:
        if not isinstance(row_value, dict):
            raise RouteMatrixError("legacy route matrix rows must be objects")
        row = cast(dict[str, object], row_value)
        if status is not None and row.get("status") != status:
            continue
        key = row.get("key")
        if not isinstance(key, str) or not key:
            raise RouteMatrixError("legacy route matrix row has no string key")
        keys.append(key)
    return tuple(keys)


def load_legacy_json(path: Path) -> LegacyRouteMatrix:
    """Retain compatibility for callers that still provide generated JSON."""

    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RouteMatrixError("legacy route matrix JSON root must be an object")
    data = cast(dict[str, object], raw)
    missing = REQUIRED_LEGACY_KEYS - set(data)
    if missing:
        raise RouteMatrixError(
            f"legacy route matrix missing required keys: {sorted(missing)}"
        )
    return LegacyRouteMatrix(
        effective_missing_backend_count=_required_legacy_integer(
            data, "effective_missing_backend_count"
        ),
        missing_backend_count=_required_legacy_integer(data, "missing_backend_count"),
        frontend_checked=_required_legacy_integer(data, "frontend_checked"),
        missing_backend_keys=_legacy_keys(data["results"], status="missing_backend"),
        effective_missing_backend_keys=_legacy_keys(
            data.get("effective_missing_backend", [])
        ),
    )


def _check_legacy_json(path: Path, *, warn_on_missing_backend: bool) -> int:
    matrix = load_legacy_json(path)
    print(
        "[route-matrix] legacy-json frontend_checked="
        f"{matrix.frontend_checked} missing_backend={matrix.missing_backend_count} "
        "effective_missing_backend="
        f"{matrix.effective_missing_backend_count}"
    )
    if warn_on_missing_backend and matrix.missing_backend_count > 0:
        print(
            "[route-matrix] warning: raw missing_backend entries: "
            f"{list(matrix.missing_backend_keys)}",
            file=sys.stderr,
        )
    if matrix.effective_missing_backend_count > 0:
        print(
            "[route-matrix] FAIL: effective missing backend routes detected: "
            f"{list(matrix.effective_missing_backend_keys)}",
            file=sys.stderr,
        )
        return 1
    return 0


def _check_markdown(path: Path, *, urls_csv_path: Path | None) -> int:
    documented_routes = parse_documented_routes(path.read_text(encoding="utf-8"))
    csv_source = (
        urls_csv_path.read_text(encoding="utf-8")
        if urls_csv_path is not None
        else export_backend_routes()
    )
    backend_routes = parse_backend_routes(csv_source)
    missing_routes = find_missing_backend_routes(documented_routes, backend_routes)
    test_files = {
        test_path for route in documented_routes for test_path in route.test_paths
    }
    print(
        f"[route-matrix] documented={len(documented_routes)} "
        f"backend={len(backend_routes)} test_files={len(test_files)}"
    )
    if missing_routes:
        for route in missing_routes:
            print(
                f"[route-matrix] FAIL line {route.line_number}: "
                f"documented backend route is missing: {route.path}",
                file=sys.stderr,
            )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the versioned API route matrix against Django's real URL "
            "inventory and its referenced tests."
        )
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(os.environ.get("ROUTE_MATRIX_PATH", DEFAULT_MATRIX)),
        help=(
            "Versioned Markdown matrix (default: docs/api_route_test_matrix.md) "
            "or a legacy generated JSON matrix"
        ),
    )
    parser.add_argument(
        "--urls-csv",
        type=Path,
        help="Existing show_urls CSV for deterministic/offline Markdown validation",
    )
    parser.add_argument(
        "--warn-on-missing-backend",
        action="store_true",
        help="Retained for legacy JSON callers; Markdown contract drift is fatal",
    )
    args = parser.parse_args()

    matrix_path = args.matrix.resolve()
    if not matrix_path.is_file():
        print(f"[route-matrix] matrix file not found: {matrix_path}", file=sys.stderr)
        return 2
    try:
        if matrix_path.suffix.lower() == ".json":
            return _check_legacy_json(
                matrix_path,
                warn_on_missing_backend=args.warn_on_missing_backend,
            )
        return _check_markdown(matrix_path, urls_csv_path=args.urls_csv)
    except (OSError, UnicodeError, json.JSONDecodeError, RouteMatrixError) as exc:
        print(f"[route-matrix] invalid contract: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
