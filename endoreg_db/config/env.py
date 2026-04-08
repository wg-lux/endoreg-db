"""
Centralized environment configuration for EndoReg-DB.

This module is the single place to read environment variables and .env files.
It avoids loading .env during pytest, and provides typed helpers.
No Django imports here to prevent early settings configuration.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Detect pytest early to avoid loading .env in test runs
IS_PYTEST = bool(os.environ.get("PYTEST_CURRENT_TEST")) or any(
    "pytest" in arg for arg in sys.argv
)
IS_STATIC_ANALYSIS = any("mypy" in arg for arg in sys.argv)

# Compute repository BASE_DIR (repo root). This file is endoreg_db/config/env.py.
BASE_DIR = Path(__file__).resolve().parents[2]
TEST_PROTECTED_ROOT = BASE_DIR / "data" / "tests" / "protected_runtime"


def _normalize_protected_runtime_paths(default_protected_root: Path) -> None:
    # LX_ANNOTATE_ENCRYPTED_DATA_DIR is the single canonical runtime root for
    # deployment-owned protected data. STORAGE_DIR and IO_DIR are normalized to
    # live inside that root even if callers provide legacy or invalid values.
    os.environ["LX_ANNOTATE_ENCRYPTED_DATA_DIR"] = str(
        Path(
            os.environ.get(
                "LX_ANNOTATE_ENCRYPTED_DATA_DIR", str(default_protected_root)
            )
        ).resolve()
    )
    protected_root = Path(os.environ["LX_ANNOTATE_ENCRYPTED_DATA_DIR"])

    storage_dir = Path(os.environ.get("STORAGE_DIR", protected_root / "storage"))
    if not storage_dir.is_absolute():
        storage_dir = (BASE_DIR / storage_dir).resolve()
    if protected_root not in (storage_dir, *storage_dir.parents):
        storage_dir = protected_root / "storage"
    os.environ["STORAGE_DIR"] = str(storage_dir)

    io_dir = Path(os.environ.get("IO_DIR", protected_root))
    if not io_dir.is_absolute():
        io_dir = (BASE_DIR / io_dir).resolve()
    if protected_root not in (io_dir, *io_dir.parents):
        io_dir = protected_root
    os.environ["IO_DIR"] = str(io_dir)


if IS_PYTEST or IS_STATIC_ANALYSIS:
    _normalize_protected_runtime_paths(TEST_PROTECTED_ROOT)

# Optional: load .env only when not under pytest
_DOTENV_LOADED = False
try:
    if not IS_PYTEST:
        import dotenv

        dotenv.load_dotenv()
        _DOTENV_LOADED = True
except Exception:
    # dotenv is optional, ignore errors
    _DOTENV_LOADED = False

if not IS_PYTEST and not IS_STATIC_ANALYSIS:
    _normalize_protected_runtime_paths(BASE_DIR / "data")


def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


def env_str(key: str, default: str = "") -> str:
    val = _get(key)
    return val if val is not None else default


def env_bool(key: str, default: bool = False) -> bool:
    val = _get(key)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int = 0) -> int:
    val = _get(key)
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except Exception:
        return default


def env_path(key: str, default_relative: str) -> Path:
    """Return an absolute path. If env is relative, resolve under BASE_DIR."""
    val = _get(key)
    if not val:
        p = BASE_DIR / default_relative
    else:
        p = Path(val)
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
    return p


def snapshot() -> Dict[str, Any]:
    """Return a snapshot of relevant config for debugging/logging."""
    keys = [
        # Core
        "DJANGO_SETTINGS_MODULE",
        "TIME_ZONE",
        # Paths
        "STORAGE_DIR",
        "ASSET_DIR",
        "STATIC_URL",
        "MEDIA_URL",
        # Dev DB
        "DEV_DB_ENGINE",
        "DEV_DB_NAME",
        # Test DB
        "TEST_DB_ENGINE",
        "TEST_DB_NAME",
        "TEST_DB_FILE",
        # Flags
        "RUN_VIDEO_TESTS",
        "SKIP_EXPENSIVE_TESTS",
    ]
    data: Dict[str, Any] = {k: os.environ.get(k) for k in keys}
    data.update(
        {
            "IS_PYTEST": IS_PYTEST,
            "DOTENV_LOADED": _DOTENV_LOADED,
            "BASE_DIR": str(BASE_DIR),
        }
    )
    return data


# Back-compat short aliases used by settings modules
ENV = os.environ.get
