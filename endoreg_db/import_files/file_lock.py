from pathlib import Path
from logging import getLogger
import os
import time
from contextlib import contextmanager

logger = getLogger(__name__)

STALE_LOCK_SECONDS = 6000  # 100 minutes - reclaim locks older than this
MAX_LOCK_WAIT_SECONDS = 90  # New: wait up to 90s for a non-stale lock to clear before skipping


def _file_lock( path: Path):
    """
    Create a file lock to prevent duplicate processing of the same video or pdf.

    This context manager creates a .lock file alongside the video file.
    If the lock file already exists, it checks if it's stale (older than
    STALE_LOCK_SECONDS) and reclaims it if necessary. If it's not stale,
    we now WAIT (up to MAX_LOCK_WAIT_SECONDS) instead of failing immediately.
    """
    lock_path = Path(str(path) + ".lock")
    fd = None
    try:
        deadline = time.time() + MAX_LOCK_WAIT_SECONDS
        while True:
            try:
                # Atomic create; fail if exists
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                break  # acquired
            except FileExistsError:
                # Check for stale lock
                age = None
                try:
                    st = os.stat(lock_path)
                    age = time.time() - st.st_mtime
                except FileNotFoundError:
                    # Race: lock removed between exists and stat; retry acquire in next loop
                    age = None

                if age is not None and age > STALE_LOCK_SECONDS:
                    try:
                        logger.warning(
                            "Stale lock detected for %s (age %.0fs). Reclaiming lock...",
                            path,
                            age,
                        )
                        lock_path.unlink()
                    except Exception as e:
                        logger.warning("Failed to remove stale lock %s: %s", lock_path, e)
                    # Loop continues and retries acquire immediately
                    continue

                # Not stale: wait until deadline, then give up gracefully
                if time.time() >= deadline:
                    raise ValueError(f"File already being processed: {path}")
                time.sleep(1.0)

        os.write(fd, b"lock")
        os.close(fd)
        fd = None
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass