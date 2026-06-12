#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REQUIRED_TOP_LEVEL_KEYS = {
    "effective_missing_backend_count",
    "missing_backend_count",
    "frontend_checked",
    "results",
}


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("route matrix JSON root must be an object")
    missing = REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise ValueError(f"route matrix missing required keys: {sorted(missing)}")
    if not isinstance(data.get("results"), list):
        raise ValueError("route matrix 'results' must be a list")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate generated route-matrix.json and fail on effective backend drift."
    )
    parser.add_argument(
        "--matrix",
        default=os.environ.get(
            "ROUTE_MATRIX_PATH", "/home/admin/dev/lx-annotate/temp/route-matrix.json"
        ),
        help="Path to generated route-matrix.json",
    )
    parser.add_argument(
        "--warn-on-missing-backend",
        action="store_true",
        help="Warn (non-fatal) if raw missing_backend_count > 0",
    )
    args = parser.parse_args()

    matrix_path = Path(args.matrix)
    if not matrix_path.exists():
        print(
            f"[route-matrix] skipped: file not found at {matrix_path}",
            file=sys.stderr,
        )
        return 0

    try:
        data = _load_json(matrix_path)
    except Exception as exc:
        print(f"[route-matrix] invalid JSON/schema: {exc}", file=sys.stderr)
        return 2

    effective_missing_backend_count = int(
        data.get("effective_missing_backend_count", 0)
    )
    missing_backend_count = int(data.get("missing_backend_count", 0))
    frontend_checked = int(data.get("frontend_checked", 0))

    print(
        "[route-matrix] frontend_checked="
        f"{frontend_checked} missing_backend={missing_backend_count} "
        f"effective_missing_backend={effective_missing_backend_count}"
    )

    if args.warn_on_missing_backend and missing_backend_count > 0:
        raw_missing = [
            row.get("key")
            for row in data.get("results", [])
            if row.get("status") == "missing_backend"
        ]
        print(
            f"[route-matrix] warning: raw missing_backend entries: {raw_missing}",
            file=sys.stderr,
        )

    if effective_missing_backend_count > 0:
        effective_missing = data.get("effective_missing_backend", [])
        keys = [row.get("key") for row in effective_missing if isinstance(row, dict)]
        print(
            f"[route-matrix] FAIL: effective missing backend routes detected: {keys}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
