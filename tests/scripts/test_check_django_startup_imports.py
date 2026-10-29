from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("valid_root", [True, False])
def test_startup_check_initializes_only_valid_test_runtime(
    tmp_path: Path,
    valid_root: bool,
) -> None:
    root = tmp_path / "runtime"
    configured_root = str(root) if valid_root else "relative-runtime"
    environment = dict(os.environ, LX_RUNTIME_ROOT=configured_root)
    result = subprocess.run(
        [sys.executable, "scripts/check_django_startup_imports.py"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    if valid_root:
        assert result.returncode == 0, result.stderr
        assert "startup-smoke: ok (" in result.stdout
        assert (root / "terminology").is_dir()
        assert (root / "storage").is_dir()
    else:
        assert result.returncode != 0
        assert "LX_RUNTIME_ROOT" in result.stderr
        assert "startup-smoke: ok" not in result.stdout
        assert not root.exists()
