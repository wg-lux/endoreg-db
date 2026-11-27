"""Configure process-isolated test paths before pytest-django initializes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.runtime_paths import build_test_run_namespace, find_stale_run_roots

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER")
RUN_NAMESPACE = build_test_run_namespace(WORKER_ID, os.getpid())
RUN_ROOT = PROJECT_ROOT / "data" / "tests" / "workers" / RUN_NAMESPACE
PROTECTED_ROOT = RUN_ROOT
STORAGE_ROOT = PROTECTED_ROOT / "storage"
STREAMABLE_ROOT = STORAGE_ROOT / "streamable_videos"

os.environ["ENDOREG_TEST_RUN_NAMESPACE"] = RUN_NAMESPACE
os.environ["LX_RUNTIME_ROOT"] = str(PROTECTED_ROOT)

from endoreg_db.utils.paths import get_runtime_paths

get_runtime_paths().ensure_directories()


# --- Cleanup of per-process run roots -------------------------------------
# Every pytest process (controller and each xdist worker) owns RUN_ROOT. A
# passing process removes its own root at exit; failing runs keep theirs for
# inspection. Roots left by crashed, killed, or failed runs are swept by the
# next controller once their owning process no longer exists.

WORKERS_ROOT = RUN_ROOT.parent
_CLEAN_EXIT_CODES: frozenset[int] = frozenset(
    {pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED}
)
_session_exitstatus: int | None = None


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        return  # Only the controller sweeps, before any worker starts.
    from endoreg_db.utils.file_operations import safe_rmtree

    for stale_root in find_stale_run_roots(WORKERS_ROOT):
        safe_rmtree(stale_root)


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    global _session_exitstatus
    _session_exitstatus = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    if _session_exitstatus not in _CLEAN_EXIT_CODES:
        return
    from endoreg_db.utils.file_operations import safe_rmtree

    safe_rmtree(RUN_ROOT)
