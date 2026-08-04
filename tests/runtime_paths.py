from __future__ import annotations


def build_test_run_namespace(worker_id: str | None, process_id: int) -> str:
    """Return a storage namespace unique to the active pytest process."""
    if process_id <= 0:
        raise ValueError("process_id must be positive")
    return f"{worker_id or 'main'}-{process_id}"
