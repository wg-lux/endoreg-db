from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path


def build_test_run_namespace(worker_id: str | None, process_id: int) -> str:
    """Return a storage namespace unique to the active pytest process."""
    if process_id <= 0:
        raise ValueError("process_id must be positive")
    return f"{worker_id or 'main'}-{process_id}"


def parse_test_run_namespace_pid(namespace: str) -> int | None:
    """Return the owning process id of a namespace, or ``None`` if it is not one."""
    _worker, separator, raw_pid = namespace.rpartition("-")
    if not separator or not raw_pid.isdigit():
        return None
    process_id = int(raw_pid)
    return process_id if process_id > 0 else None


def is_process_alive(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Exists but belongs to another user.
    return True


def find_stale_run_roots(
    workers_root: Path,
    *,
    is_alive: Callable[[int], bool] = is_process_alive,
) -> list[Path]:
    """Return per-process run roots whose owning pytest process has exited.

    Directories that do not follow the namespace format are never returned, so
    unrelated content is left untouched.
    """
    if not workers_root.is_dir():
        return []
    stale: list[Path] = []
    for entry in sorted(workers_root.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        process_id = parse_test_run_namespace_pid(entry.name)
        if process_id is not None and not is_alive(process_id):
            stale.append(entry)
    return stale
