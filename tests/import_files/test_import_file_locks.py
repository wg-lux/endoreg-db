from pathlib import Path
import subprocess
import sys
from importlib import import_module

import pytest

from endoreg_db.import_files.context.file_lock import content_hash_lock, file_lock

lock_module = import_module("endoreg_db.import_files.context.file_lock")


@pytest.mark.parametrize("content_lock", [False, True])
def test_timeout_preserves_owner_and_lock_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content_lock: bool
) -> None:
    monkeypatch.setattr(lock_module, "MAX_LOCK_WAIT_SECONDS", 0)
    name = "a" * 64 if content_lock else "source.pdf"
    acquire = content_hash_lock if content_lock else file_lock
    path = tmp_path / f"{name}.lock"
    with acquire(name, lock_root=tmp_path):
        inode = path.stat().st_ino
        for _ in range(2):
            with pytest.raises(TimeoutError):
                with acquire(name, lock_root=tmp_path):
                    pytest.fail("A waiter entered while the owner still held the lock")
            assert path.stat().st_ino == inode
    with acquire(name, lock_root=tmp_path):
        assert path.stat().st_ino == inode


def test_worker_death_releases_lock_without_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lock_module, "MAX_LOCK_WAIT_SECONDS", 0)
    path = tmp_path / "source.pdf.lock"
    script = (
        "import fcntl, sys\n"
        "with open(sys.argv[1], 'a') as f:\n"
        " fcntl.flock(f, fcntl.LOCK_EX)\n"
        " print('locked', flush=True)\n"
        " sys.stdin.read()\n"
    )
    with subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as worker:
        try:
            assert worker.stdout is not None
            assert worker.stdout.readline().strip() == "locked"
            inode = path.stat().st_ino
            with pytest.raises(TimeoutError):
                with file_lock("source.pdf", lock_root=tmp_path):
                    pytest.fail("Live worker lost its lock")
        finally:
            worker.kill()
            worker.wait(timeout=10)
    with file_lock("source.pdf", lock_root=tmp_path):
        assert path.stat().st_ino == inode


@pytest.mark.parametrize("digest", ["", "../escape", "a" * 63, "A" * 64])
def test_invalid_content_identity_is_rejected(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        with content_hash_lock(digest, lock_root=tmp_path):
            pytest.fail("Invalid digest accepted")
    assert not list(tmp_path.iterdir())
