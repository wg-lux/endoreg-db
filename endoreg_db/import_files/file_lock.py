from contextlib import contextmanager
from pathlib import Path
import os
import time
from logging import getLogger
from endoreg_db.import_files.processing import ErrorCleanup

logger = getLogger(__name__)

STALE_LOCK_SECONDS = 6000
MAX_LOCK_WAIT_SECONDS = 90
    

@contextmanager
def _file_lock(path: Path, file_type: str):
    """
    Create a file lock to prevent duplicate processing of the same video or pdf.
    """
    lock_path = Path(str(path) + ".lock")
    fd = None
    try:
        deadline = time.time() + MAX_LOCK_WAIT_SECONDS
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                break
            except FileExistsError:
                age = None
                try:
                    st = os.stat(lock_path)
                    age = time.time() - st.st_mtime
                except FileNotFoundError:
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
                    continue

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
            if file_type is "video":
                ok = ErrorCleanup("video")
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass
