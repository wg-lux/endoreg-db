from __future__ import annotations

import errno
import os
import time
from pathlib import Path

import pytest

from endoreg_db.import_files.context.file_lock import STALE_LOCK_SECONDS
from endoreg_db.services.reconciliation import ReconciliationService
from endoreg_db.utils.filesystem.file_operations import atomic_copy_file


@pytest.mark.unit
def test_atomic_copy_file_removes_partial_temp_artifact_on_enospc(
    monkeypatch, tmp_path
):
    source = tmp_path / "source.bin"
    destination = tmp_path / "dest" / "target.bin"
    source.write_bytes(b"payload")

    original_copy2 = __import__("shutil").copy2

    def failing_copy2(src: str, dst: str):
        Path(dst).write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("shutil.copy2", failing_copy2)

    with pytest.raises(OSError) as exc_info:
        atomic_copy_file(source=source, destination=destination)

    assert exc_info.value.errno == errno.ENOSPC
    assert destination.exists() is False
    assert list(destination.parent.glob("target.bin.tmp.*")) == []
    monkeypatch.setattr("shutil.copy2", original_copy2)


@pytest.mark.unit
def test_reconciliation_cleans_stale_streamable_temp_artifacts(monkeypatch, tmp_path):
    import endoreg_db.services.reconciliation as reconciliation_module

    sensitive_dir = tmp_path / "sensitive_videos"
    anonym_dir = tmp_path / "processed_videos_final"
    transcoding_dir = tmp_path / "temp"
    streamable_root = tmp_path / "streamable_videos"
    streamable_raw = streamable_root / "raw"
    streamable_processed = streamable_root / "processed"
    for path in (
        sensitive_dir,
        anonym_dir,
        transcoding_dir,
        streamable_root,
        streamable_raw,
        streamable_processed,
    ):
        path.mkdir(parents=True, exist_ok=True)

    stale_raw_tmp = streamable_raw / "abc.mp4.tmp.1234"
    stale_processed_part = streamable_processed / "def.part.mp4"
    stale_root_tmp = streamable_root / "ghi.tmp"
    for path in (stale_raw_tmp, stale_processed_part, stale_root_tmp):
        path.write_bytes(b"orphan")
        old = time.time() - (STALE_LOCK_SECONDS + 10)
        os.utime(path, (old, old))

    monkeypatch.setattr(
        reconciliation_module,
        "data_paths",
        {
            **reconciliation_module.data_paths,
            "sensitive_video": sensitive_dir,
            "anonym_video": anonym_dir,
            "transcoding": transcoding_dir,
        },
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "STREAMABLE_VIDEO_ROOT",
        streamable_root,
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "STREAMABLE_RAW_VIDEO_ROOT",
        streamable_raw,
        raising=True,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "STREAMABLE_PROCESSED_VIDEO_ROOT",
        streamable_processed,
        raising=True,
    )

    removed = ReconciliationService().cleanup_orphaned_artifacts()

    assert removed == 3
    assert not stale_raw_tmp.exists()
    assert not stale_processed_part.exists()
    assert not stale_root_tmp.exists()
