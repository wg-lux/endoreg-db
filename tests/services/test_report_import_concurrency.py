from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from typing import NoReturn

import pytest

from endoreg_db.import_files.report_import_service import ReportImportService
from endoreg_db.utils.file_operations import (
    advisory_file_lock,
    atomic_report_source_snapshot,
)


@pytest.mark.unit
def test_report_import_snapshots_after_path_lock_and_before_content_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import endoreg_db.import_files.report_import_service as report_import_module

    source = tmp_path / "source.pdf"
    snapshot_path = tmp_path / "sensitive" / "snapshot.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    events: list[str] = []

    @contextmanager
    def fake_file_lock(path: Path) -> Generator[None]:
        assert path == source
        events.append("path_lock")
        yield

    def fake_snapshot(path: Path, sensitive_root: Path) -> SimpleNamespace:
        assert path == source
        events.append("snapshot")
        return SimpleNamespace(path=snapshot_path, sha256="a" * 64)

    @contextmanager
    def fake_content_lock(
        file_hash: str,
        lock_root: Path | None = None,
    ) -> Generator[None]:
        assert file_hash == "a" * 64
        events.append("content_lock")
        yield

    def stop_after_lock_ordering(
        self: ReportImportService,
        ctx: object,
    ) -> NoReturn:
        raise RuntimeError("stop after lock ordering")

    monkeypatch.setattr(report_import_module, "report_source_lock", fake_file_lock)
    monkeypatch.setattr(
        report_import_module,
        "create_sensitive_report_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(
        report_import_module,
        "report_content_hash_lock",
        fake_content_lock,
    )
    monkeypatch.setattr(
        ReportImportService,
        "_get_existing_completed_report",
        stop_after_lock_ordering,
    )
    monkeypatch.setattr(
        report_import_module,
        "_sensitive_report_dir",
        lambda: tmp_path / "sensitive",
    )

    with pytest.raises(RuntimeError, match="stop after lock ordering"):
        ReportImportService().import_and_anonymize(source, "dummy-center")

    assert events == ["path_lock", "snapshot", "content_lock"]


@pytest.mark.unit
def test_parallel_snapshots_are_isolated_and_content_identical(
    tmp_path: Path,
) -> None:
    payload = (b"%PDF-1.4\nparallel-report\n" * 8192) + b"%%EOF\n"
    expected_hash = hashlib.sha256(payload).hexdigest()
    source = tmp_path / "source.pdf"
    source.write_bytes(payload)

    def create_snapshot(index: int) -> tuple[Path, str]:
        destination = tmp_path / f"attempt-{index}" / "source.pdf"
        snapshot = atomic_report_source_snapshot(
            source=source,
            destination=destination,
            chunk_size=1024,
        )
        return snapshot.path, snapshot.sha256

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create_snapshot, range(8)))

    assert len({path for path, _digest in results}) == 8
    assert {digest for _path, digest in results} == {expected_hash}
    assert all(path.read_bytes() == payload for path, _digest in results)


@pytest.mark.unit
def test_advisory_lock_is_process_owned_and_not_age_reclaimed(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "report.lock"
    entered = Event()
    release = Event()

    def hold_lock() -> None:
        with advisory_file_lock(lock_path=lock_path):
            entered.set()
            assert release.wait(timeout=2)

    holder = Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=2)
    try:
        with (
            pytest.raises(TimeoutError, match="Timed out waiting"),
            advisory_file_lock(
                lock_path=lock_path,
                timeout_seconds=0.05,
                poll_interval_seconds=0.01,
            ),
        ):
            pass
    finally:
        release.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    assert lock_path.exists()
    with advisory_file_lock(lock_path=lock_path, timeout_seconds=0.1):
        pass


@pytest.mark.unit
def test_advisory_lock_releases_after_process_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "report-process.lock"
    child_script = """
import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "endoreg_db.config.settings.test")
import django
django.setup()

from endoreg_db.utils.filesystem.file_operations import advisory_file_lock

with advisory_file_lock(lock_path=Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_script, str(lock_path)],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        ready_line = child.stdout.readline().strip()
        if ready_line != "locked":
            assert child.stderr is not None
            pytest.fail(f"lock child failed before readiness: {child.stderr.read()}")
        with (
            pytest.raises(TimeoutError, match="Timed out waiting"),
            advisory_file_lock(
                lock_path=lock_path,
                timeout_seconds=0.05,
                poll_interval_seconds=0.01,
            ),
        ):
            pass
    finally:
        if child.stdin is not None and child.poll() is None:
            child.stdin.write("\n")
            child.stdin.flush()
        child.wait(timeout=5)

    assert child.returncode == 0
    with advisory_file_lock(lock_path=lock_path, timeout_seconds=0.1):
        pass
