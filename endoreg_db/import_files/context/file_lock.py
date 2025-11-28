from contextlib import contextmanager
from pathlib import Path
import os
import time
from logging import getLogger
import errno
import shutil

logger = getLogger(__name__)

STALE_LOCK_SECONDS = 6000
MAX_LOCK_WAIT_SECONDS = 90


@contextmanager
def file_lock(path: Path):
    """
    Create a file lock to prevent duplicate processing of the same file.

    Lock is created *next to* the source file: "<path>.lock".
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
            if lock_path.exists():
                lock_path.unlink()
        except OSError:
            pass


def quarantine(source: Path, qdir: Path) -> Path:
    """
    Move file to quarantine directory to prevent re-processing.

    Returns the *new* path in qdir.
    """
    
    qdir.mkdir(parents=True, exist_ok=True)
    target = qdir / source.name
    try:
        # Try atomic rename first (fastest when on same filesystem)
        source.rename(target)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            # Cross-device move, fall back to shutil.move which copies+removes
            shutil.move(str(source), str(target))
        else:
            raise

    # Clean any old lock on the ORIGINAL location
    lock_path = Path(str(source) + ".lock")
    if lock_path.exists():
        lock_path.unlink()

    return target


def unquarantine(source: Path, target_dir: Path) -> Path:
    """
    Move file from quarantine back to its original directory (or any target_dir).

    `source` is the current quarantine path, `target_dir` is where it should go.
    """
    target = target_dir / source.name
    try:
        source.rename(target)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            shutil.move(str(source), str(target))
        else:
            raise

    # Clean any lock that might still exist beside the quarantine file
    lock_path = Path(str(source) + ".lock")
    if lock_path.exists():
        lock_path.unlink()

    return target
