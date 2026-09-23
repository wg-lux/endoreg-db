from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path

from endoreg_db.utils.file_operations import advisory_file_lock
from endoreg_db.utils.paths import get_runtime_paths

MAX_LOCK_WAIT_SECONDS = 90
# Retained for reconciliation's artifact-age policy, never for lock ownership.
STALE_LOCK_SECONDS = 10000


@contextmanager
def file_lock(name: Path | str, lock_root: Path | None = None) -> Generator[None]:
    """Serialize source access with a process-owned, persistent advisory lock.

    Never unlink these lock files: waiters and owners must share one inode.
    All import workers must be restarted together when migrating from the
    legacy existence-based locks.
    """
    root = lock_root if lock_root is not None else get_runtime_paths().locks
    with advisory_file_lock(
        lock_path=root / f"{Path(name).name}.lock",
        timeout_seconds=MAX_LOCK_WAIT_SECONDS,
    ):
        yield


@contextmanager
def content_hash_lock(file_hash: str, lock_root: Path | None = None) -> Generator[None]:
    """Serialize same-content imports without deleting another owner's lock."""
    if len(file_hash) != 64 or any(c not in "0123456789abcdef" for c in file_hash):
        raise ValueError("Content lock requires a lowercase SHA-256 digest")
    with file_lock(file_hash, lock_root=lock_root):
        yield
