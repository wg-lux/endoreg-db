from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from endoreg_db.models import VideoFile
from endoreg_db.services import streamable_media
from endoreg_db.utils import paths as paths_module


def test_streamable_materialization_never_moves_canonical_source() -> None:
    source = Path(streamable_media.__file__).read_text(encoding="utf-8")

    assert "atomic_write_file(" in source
    assert "atomic_move_file(" not in source
    assert 'open(target_path, "wb")' not in source


class FakeEncryptedStorage:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get_plaintext_size(self, name: str) -> int:
        return len(self.payload)

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ):
        if end < start:
            return iter(())
        selected = self.payload[start : end + 1]
        for offset in range(0, len(selected), chunk_size):
            yield selected[offset : offset + chunk_size]


class StubFieldFile:
    def __init__(self, storage, name: str):
        self.storage = storage
        self.name = name

    @property
    def size(self):
        return self.storage.get_plaintext_size(self.name)


class StubVideo:
    class StorageMode:
        APP_ENCRYPTED = "app_encrypted"
        FS_ENCRYPTED_STREAMABLE = "fs_encrypted_streamable"

    def __init__(self, *, raw_file, processed_file):
        self.pk = 123
        self.video_hash = "rawhash"
        self.processed_video_hash = "processedhash"
        self.raw_file = raw_file
        self.processed_file = processed_file
        self.streamable_relative_path = ""
        self.processed_streamable_relative_path = ""
        self.storage_mode = self.StorageMode.APP_ENCRYPTED

    def save(self, update_fields):
        self.saved_update_fields = update_fields


def test_sync_video_streamable_artifacts_materializes_plaintext_from_encrypted_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_payload = b"\x00\x00\x00\x18ftypmp42raw"
    processed_payload = b"\x00\x00\x00\x18ftypmp42processed"

    video = StubVideo(
        raw_file=StubFieldFile(
            FakeEncryptedStorage(raw_payload), "videos/raw-source.mp4"
        ),
        processed_file=StubFieldFile(
            FakeEncryptedStorage(processed_payload),
            "videos/processed-source.mp4",
        ),
    )

    # 🔑 CRITICAL: stay inside STORAGE_DIR to satisfy path contract
    base_root = paths_module.STORAGE_DIR / "test_streamable"
    raw_root = base_root / "streamable_videos" / "raw"
    processed_root = base_root / "streamable_videos" / "processed"

    raw_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        streamable_media,
        "STREAMABLE_RAW_VIDEO_ROOT",
        raw_root,
    )
    monkeypatch.setattr(
        streamable_media,
        "STREAMABLE_PROCESSED_VIDEO_ROOT",
        processed_root,
    )

    monkeypatch.setattr(
        streamable_media,
        "resolve_storage_policy",
        lambda payload_kind: streamable_media.StoragePolicy.FS_STREAMABLE,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    # ✅ Correct update fields
    assert update_fields == [
        "streamable_relative_path",
        "processed_streamable_relative_path",
        "storage_mode",
    ]

    # ✅ Storage mode switched
    assert video.storage_mode == video.StorageMode.FS_ENCRYPTED_STREAMABLE

    # ✅ Resolve absolute paths via STORAGE_DIR (correct contract!)
    raw_target = paths_module.STORAGE_DIR / video.streamable_relative_path
    processed_target = (
        paths_module.STORAGE_DIR / video.processed_streamable_relative_path
    )

    assert raw_target.read_bytes() == raw_payload
    assert processed_target.read_bytes() == processed_payload


def test_sync_video_streamable_artifacts_clears_paths_when_not_streamable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = StubVideo(
        raw_file=StubFieldFile(FakeEncryptedStorage(b"x"), "videos/raw.mp4"),
        processed_file=StubFieldFile(FakeEncryptedStorage(b"y"), "videos/proc.mp4"),
    )

    video.streamable_relative_path = "foo/bar.mp4"
    video.processed_streamable_relative_path = "baz/qux.mp4"

    monkeypatch.setattr(
        streamable_media,
        "resolve_storage_policy",
        lambda payload_kind: streamable_media.StoragePolicy.APP_ENCRYPTED,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    assert "streamable_relative_path" in update_fields
    assert "processed_streamable_relative_path" in update_fields

    assert video.streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""
    assert video.storage_mode == video.StorageMode.APP_ENCRYPTED


def test_sync_video_streamable_artifacts_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"abc123"

    video = StubVideo(
        raw_file=StubFieldFile(FakeEncryptedStorage(payload), "videos/raw.mp4"),
        processed_file=None,
    )

    base_root = paths_module.STORAGE_DIR / "test_streamable_idempotent"
    raw_root = base_root / "streamable_videos" / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        streamable_media,
        "STREAMABLE_RAW_VIDEO_ROOT",
        raw_root,
    )
    monkeypatch.setattr(
        streamable_media,
        "resolve_storage_policy",
        lambda payload_kind: streamable_media.StoragePolicy.FS_STREAMABLE,
    )

    # first run
    first_update = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    # second run (should be no-op)
    second_update = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    assert first_update
    assert second_update == []  # 🔥 idempotency guarantee
