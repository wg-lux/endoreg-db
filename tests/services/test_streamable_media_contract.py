from __future__ import annotations

import importlib
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest

from endoreg_db.models import VideoFile
from endoreg_db.services import streamable_media
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.storage_profile import StoragePolicy


def _streamable_policy(_payload_kind: object) -> StoragePolicy:
    return StoragePolicy.FS_STREAMABLE


def _app_encrypted_policy(_payload_kind: object) -> StoragePolicy:
    return StoragePolicy.APP_ENCRYPTED


def test_streamable_materialization_never_moves_canonical_source() -> None:
    source_file = streamable_media.__file__
    assert source_file is not None
    source = Path(source_file).read_text(encoding="utf-8")

    assert "atomic_write_file(" in source
    assert "atomic_move_file(" not in source
    assert 'open(target_path, "wb")' not in source


def test_streamable_processed_root_constant_uses_processed_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    protected_root = tmp_path / "protected"
    storage_root = protected_root / "storage"
    data_root = tmp_path / "public"
    processed_root = storage_root / "streamable_videos" / "processed-custom"
    raw_root = storage_root / "streamable_videos" / "raw-custom"

    monkeypatch.setenv("LX_ANNOTATE_ENCRYPTED_DATA_DIR", str(protected_root))
    monkeypatch.setenv("STORAGE_DIR", str(storage_root))
    monkeypatch.setenv("DATA_DIR", str(data_root))
    monkeypatch.setenv("PROTECTED_MEDIA_ROOT", str(storage_root))
    monkeypatch.setenv("LX_ANNOTATE_STREAMABLE_VIDEO_RAW_ROOT", str(raw_root))
    monkeypatch.setenv(
        "LX_ANNOTATE_STREAMABLE_VIDEO_PROCESSED_ROOT",
        str(processed_root),
    )

    reloaded = importlib.reload(streamable_media)

    assert reloaded.STREAMABLE_RAW_VIDEO_ROOT == raw_root.resolve()
    assert reloaded.STREAMABLE_PROCESSED_VIDEO_ROOT == processed_root.resolve()
    assert (
        reloaded.STREAMABLE_PROCESSED_VIDEO_ROOT != reloaded.STREAMABLE_RAW_VIDEO_ROOT
    )


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
    ) -> Iterable[bytes]:
        if end < start:
            return iter(())
        selected = self.payload[start : end + 1]
        for offset in range(0, len(selected), chunk_size):
            yield selected[offset : offset + chunk_size]


class StubFieldFile:
    def __init__(self, storage: FakeEncryptedStorage, name: str) -> None:
        self.storage = storage
        self.name = name

    @property
    def size(self) -> int:
        return self.storage.get_plaintext_size(self.name)


class StubVideo:
    class StorageMode:
        ENCRYPTED = "app_encrypted"
        STREAMABLE = "fs_encrypted_streamable"

    def __init__(
        self,
        *,
        raw_file: StubFieldFile,
        processed_file: StubFieldFile | None,
    ) -> None:
        self.pk = 123
        self.video_hash = "rawhash"
        self.processed_video_hash = "processedhash"
        self.raw_file = raw_file
        self.processed_file = processed_file
        self.raw_streamable_relative_path = ""
        self.processed_streamable_relative_path = ""
        self.storage_mode = self.StorageMode.ENCRYPTED

    def save(self, update_fields: list[str]) -> None:
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
        _streamable_policy,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    # ✅ Correct update fields
    assert update_fields == [
        "raw_streamable_relative_path",
        "processed_streamable_relative_path",
        "storage_mode",
    ]

    # ✅ Storage mode switched
    assert video.storage_mode == video.StorageMode.STREAMABLE

    # ✅ Resolve absolute paths via STORAGE_DIR (correct contract!)
    raw_target = paths_module.STORAGE_DIR / video.raw_streamable_relative_path
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

    video.raw_streamable_relative_path = "foo/bar.mp4"
    video.processed_streamable_relative_path = "baz/qux.mp4"

    monkeypatch.setattr(
        streamable_media,
        "resolve_storage_policy",
        _app_encrypted_policy,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    assert "raw_streamable_relative_path" in update_fields
    assert "processed_streamable_relative_path" in update_fields

    assert video.raw_streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""
    assert video.storage_mode == video.StorageMode.ENCRYPTED


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
        _streamable_policy,
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
