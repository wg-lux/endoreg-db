from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest

from endoreg_db.services import streamable_media as sm


class DummyStorageMode:
    FS_ENCRYPTED_STREAMABLE = "fs_encrypted_streamable"
    APP_ENCRYPTED = "app_encrypted"


class DummyFieldFile:
    def __init__(self, name: str):
        self.name = name


class DummyVideo:
    StorageMode = DummyStorageMode

    def __init__(self):
        self.raw_payload = b"\x00\x00\x00\x20ftypmp42raw"
        self.processed_payload = b"\x00\x00\x00\x20ftypmp42processed"
        self.pk = 1
        self.video_hash = hashlib.sha256(self.raw_payload).hexdigest()
        self.processed_video_hash = hashlib.sha256(self.processed_payload).hexdigest()
        self.raw_file = DummyFieldFile("raw/input.mp4")
        self.processed_file = DummyFieldFile("processed/input.mp4")
        self.raw_streamable_relative_path = ""
        self.processed_streamable_relative_path = ""
        self.storage_mode = DummyStorageMode.APP_ENCRYPTED
        self.saved_update_fields = None

    def save(self, update_fields=None):
        self.saved_update_fields = list(update_fields or [])


@pytest.fixture
def video():
    return DummyVideo()


@pytest.fixture
def streamable_roots(tmp_path, monkeypatch):
    raw_root = tmp_path / "streamable_videos" / "raw"
    processed_root = tmp_path / "streamable_videos" / "processed"

    monkeypatch.setattr(sm, "_streamable_raw_video_root", lambda: raw_root)
    monkeypatch.setattr(sm, "_streamable_processed_video_root", lambda: processed_root)

    monkeypatch.setattr(
        sm,
        "_streamable_relative_path",
        lambda path: Path(path).relative_to(tmp_path).as_posix(),
    )

    return SimpleNamespace(
        root=tmp_path,
        raw_root=raw_root,
        processed_root=processed_root,
    )


def test_materialize_refuses_encrypted_streamable(tmp_path, monkeypatch):
    target = tmp_path / "bad.mp4"

    monkeypatch.setattr(sm, "field_file_size", lambda field_file: 8)
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        lambda field_file, start, end: iter([sm.LX_ENCRYPTED_MAGIC, b"ciphertext"]),
    )

    with pytest.raises(RuntimeError, match="Refusing encrypted streamable artifact"):
        sm._materialize_streamable_target(DummyFieldFile("raw/input.mp4"), target)
    assert not target.exists()


def test_sync_materializes_plaintext_raw_and_sets_streamable_mode(
    video, streamable_roots, monkeypatch
):
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        lambda kind: sm.StoragePolicy.FS_STREAMABLE,
    )
    monkeypatch.setattr(sm, "field_file_size", lambda field_file: 12)
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        lambda field_file, start, end: iter([video.raw_payload]),
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"

    assert target.exists()
    assert target.read_bytes().startswith(b"\x00\x00\x00\x20ftyp")
    assert (
        video.raw_streamable_relative_path
        == f"streamable_videos/raw/{video.video_hash}.mp4"
    )
    assert video.storage_mode == DummyStorageMode.FS_ENCRYPTED_STREAMABLE
    assert "raw_streamable_relative_path" in update_fields
    assert "storage_mode" in update_fields
    assert video.saved_update_fields is not None
    assert "storage_mode" in video.saved_update_fields


def test_sync_is_idempotent_and_does_not_rewrite_existing_plaintext(
    video, streamable_roots, monkeypatch
):
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        lambda kind: sm.StoragePolicy.FS_STREAMABLE,
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.raw_payload)
    before = target.stat().st_mtime_ns

    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.storage_mode = DummyStorageMode.FS_ENCRYPTED_STREAMABLE

    def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "should not rematerialize existing plaintext streamable file"
        )

    monkeypatch.setattr(sm, "_materialize_streamable_target", fail_if_called)

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert target.stat().st_mtime_ns == before
    assert update_fields == []


def test_sync_updates_db_path_without_rewriting_existing_plaintext(
    video, streamable_roots, monkeypatch
):
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        lambda kind: sm.StoragePolicy.FS_STREAMABLE,
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.raw_payload)

    video.raw_streamable_relative_path = ""

    def fail_if_called(*args, **kwargs):
        raise AssertionError("should not rewrite existing plaintext file")

    monkeypatch.setattr(sm, "_materialize_streamable_target", fail_if_called)

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert (
        video.raw_streamable_relative_path
        == f"streamable_videos/raw/{video.video_hash}.mp4"
    )
    assert "raw_streamable_relative_path" in update_fields
    assert "storage_mode" in update_fields


def test_sync_repairs_existing_plaintext_with_wrong_hash(
    video, streamable_roots, monkeypatch
):
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        lambda kind: sm.StoragePolicy.FS_STREAMABLE,
    )
    monkeypatch.setattr(
        sm, "field_file_size", lambda field_file: len(video.raw_payload)
    )
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        lambda field_file, start, end: iter([video.raw_payload]),
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x00\x00\x00\x20ftypmp42wrong")
    before = target.stat().st_mtime_ns

    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.storage_mode = DummyStorageMode.FS_ENCRYPTED_STREAMABLE

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert target.read_bytes() == video.raw_payload
    assert target.stat().st_mtime_ns != before
    assert update_fields == []


def test_dry_run_does_not_write_file_or_set_streamable_mode(
    video, streamable_roots, monkeypatch
):
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        lambda kind: sm.StoragePolicy.FS_STREAMABLE,
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=False,
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"

    assert not target.exists()
    assert video.saved_update_fields is None
    assert video.storage_mode == DummyStorageMode.APP_ENCRYPTED
    assert "storage_mode" not in update_fields


def test_skipped_policy_clears_stale_streamable_paths_and_app_encrypted_mode(
    video, streamable_roots, monkeypatch
):
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        lambda kind: sm.StoragePolicy.APP_ENCRYPTED,
    )

    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.processed_streamable_relative_path = (
        f"streamable_videos/processed/{video.processed_video_hash}.mp4"
    )
    video.storage_mode = DummyStorageMode.FS_ENCRYPTED_STREAMABLE

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=True,
        save=True,
    )

    assert video.raw_streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""
    assert video.storage_mode == DummyStorageMode.APP_ENCRYPTED
    assert "raw_streamable_relative_path" in update_fields
    assert "processed_streamable_relative_path" in update_fields
    assert "storage_mode" in update_fields
