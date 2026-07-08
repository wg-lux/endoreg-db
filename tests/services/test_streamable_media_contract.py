from __future__ import annotations

import importlib
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

import pytest

from endoreg_db.models import VideoFile
from endoreg_db.services import streamable_media
from endoreg_db.services import streamable_media_transcoding
from endoreg_db.utils import paths as paths_module
from endoreg_db.utils.storage_profile import StoragePolicy


def _streamable_policy(_payload_kind: object) -> StoragePolicy:
    return StoragePolicy.FS_STREAMABLE


def _app_encrypted_policy(_payload_kind: object) -> StoragePolicy:
    return StoragePolicy.APP_ENCRYPTED


def _copy_streamable_transcode(
    source_path: Path, target_path: Path, **_: object
) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(source_path.read_bytes())
    return target_path


def test_streamable_materialization_never_moves_canonical_source() -> None:
    source_file = streamable_media.__file__
    transcode_file = streamable_media_transcoding.__file__
    assert source_file is not None
    assert transcode_file is not None
    materialization_source = Path(source_file).read_text(encoding="utf-8")
    transcode_source = Path(transcode_file).read_text(encoding="utf-8")

    assert "atomic_write_file(" in materialization_source
    assert "atomic_move_file(" in transcode_source
    assert "source=ffmpeg_source_path" not in materialization_source
    assert "source=source_path" not in materialization_source
    assert 'open(target_path, "wb")' not in materialization_source


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


class ExplodingEncryptedStorage:
    def get_plaintext_size(self, name: str) -> int:
        raise ValueError("Unsupported encrypted file format")

    def iter_decrypted_range(
        self,
        name: str,
        *,
        start: int,
        end: int,
        chunk_size: int,
    ) -> Iterable[bytes]:
        raise ValueError("Unsupported encrypted file format")


class _ReadableContent(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class RehomingStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, name: str) -> str:
        return str(self.root / name)

    def exists(self, name: str) -> bool:
        return (self.root / name).exists()

    def delete(self, name: str) -> None:
        (self.root / name).unlink(missing_ok=True)

    def save(self, name: str, content: object) -> str:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as destination:
            source = cast(_ReadableContent, getattr(content, "file", content))
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                destination.write(chunk)
        return name

    def repair_plaintext_file(self, name: str) -> bool:
        return False


class StubFieldFile:
    def __init__(
        self,
        storage: FakeEncryptedStorage | ExplodingEncryptedStorage | RehomingStorage,
        name: str,
    ) -> None:
        self.storage = storage
        self.name = name

    @property
    def size(self) -> int:
        if isinstance(self.storage, RehomingStorage):
            return (self.storage.root / self.name).stat().st_size
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


def test_sync_video_streamable_artifacts_removes_legacy_streamable_paths(
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

    base_root = paths_module.STORAGE_DIR / "test_streamable"
    raw_root = base_root / "streamable_videos" / "raw"
    processed_root = base_root / "streamable_videos" / "processed"

    raw_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)
    raw_legacy = raw_root / "legacy-raw.mp4"
    processed_legacy = processed_root / "legacy-processed.mp4"
    raw_legacy.write_bytes(raw_payload)
    processed_legacy.write_bytes(processed_payload)
    video.raw_streamable_relative_path = raw_legacy.relative_to(
        paths_module.STORAGE_DIR
    ).as_posix()
    video.processed_streamable_relative_path = processed_legacy.relative_to(
        paths_module.STORAGE_DIR
    ).as_posix()
    video.storage_mode = video.StorageMode.STREAMABLE

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
    monkeypatch.setattr(
        streamable_media,
        "_transcode_streamable_mp4",
        _copy_streamable_transcode,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    assert update_fields == [
        "raw_streamable_relative_path",
        "processed_streamable_relative_path",
        "storage_mode",
    ]
    assert video.storage_mode == video.StorageMode.ENCRYPTED
    assert video.raw_streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""
    assert not raw_legacy.exists()
    assert not processed_legacy.exists()


def test_sync_video_streamable_artifacts_does_not_materialize_from_local_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processed_payload = b"\x00\x00\x00\x18ftypisomprocessed"
    processed_legacy = (
        paths_module.STORAGE_DIR
        / "test_streamable_plaintext_source"
        / "streamable_videos"
        / "processed"
        / "plain.mp4"
    )
    processed_legacy.parent.mkdir(parents=True, exist_ok=True)
    processed_legacy.write_bytes(processed_payload)

    video = StubVideo(
        raw_file=StubFieldFile(FakeEncryptedStorage(b"raw"), "videos/raw.mp4"),
        processed_file=StubFieldFile(
            ExplodingEncryptedStorage(),
            "processed_videos_final/plain.mp4",
        ),
    )
    video.processed_streamable_relative_path = processed_legacy.relative_to(
        paths_module.STORAGE_DIR
    ).as_posix()
    video.storage_mode = video.StorageMode.STREAMABLE

    def policy(payload_kind: object) -> StoragePolicy:
        if str(payload_kind) == "video_processed":
            return StoragePolicy.FS_STREAMABLE
        return StoragePolicy.APP_ENCRYPTED

    monkeypatch.setattr(
        streamable_media,
        "STREAMABLE_PROCESSED_VIDEO_ROOT",
        processed_legacy.parent,
    )
    monkeypatch.setattr(streamable_media, "resolve_storage_policy", policy)
    monkeypatch.setattr(
        streamable_media,
        "_transcode_streamable_mp4",
        _copy_streamable_transcode,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    assert update_fields == [
        "processed_streamable_relative_path",
        "storage_mode",
    ]
    assert video.processed_streamable_relative_path == ""
    assert video.storage_mode == video.StorageMode.ENCRYPTED
    assert not processed_legacy.exists()


def test_sync_rehomes_canonical_processed_file_from_legacy_streamable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"\x00\x00\x00\x18ftypisomprocessed"
    root = paths_module.STORAGE_DIR / "test_streamable_rehome"
    legacy_relative = "streamable_videos/processed/shared.mp4"
    legacy_path = root / legacy_relative
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(payload)

    video = StubVideo(
        raw_file=StubFieldFile(FakeEncryptedStorage(b"raw"), "videos/raw.mp4"),
        processed_file=StubFieldFile(RehomingStorage(root), legacy_relative),
    )
    video.processed_streamable_relative_path = legacy_relative
    video.storage_mode = video.StorageMode.STREAMABLE

    monkeypatch.setattr(
        streamable_media,
        "resolve_storage_policy",
        _streamable_policy,
    )

    def resolve_existing_protected_media_path(relative_path: str) -> Path:
        return root / relative_path

    monkeypatch.setattr(
        streamable_media.path_utils,
        "resolve_existing_protected_media_path",
        resolve_existing_protected_media_path,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video),
        include_raw=False,
        include_processed=True,
    )

    assert "processed_file" in update_fields
    assert "processed_streamable_relative_path" in update_fields
    assert video.processed_streamable_relative_path == ""
    processed_file = video.processed_file
    assert processed_file is not None
    assert not processed_file.name.startswith("streamable_videos/")
    assert not legacy_path.exists()
    assert (root / processed_file.name).read_bytes() == payload


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
    payload = b"\x00\x00\x00\x18ftypmp42moovmdat"

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
    monkeypatch.setattr(
        streamable_media,
        "_transcode_streamable_mp4",
        _copy_streamable_transcode,
    )

    # first run
    first_update = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    # second run (should be no-op)
    second_update = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video)
    )

    assert first_update == []
    assert second_update == []


def test_sync_force_regenerates_processed_hls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = StubVideo(
        raw_file=StubFieldFile(FakeEncryptedStorage(b"raw"), "videos/raw.mp4"),
        processed_file=StubFieldFile(
            FakeEncryptedStorage(b"processed"),
            "processed_videos_final/video.mp4",
        ),
    )
    calls: list[tuple[VideoFile, bool]] = []

    def fake_materialize_processed_hls(
        target_video: VideoFile,
        *,
        force: bool,
    ) -> None:
        calls.append((target_video, force))

    monkeypatch.setattr(
        streamable_media,
        "_materialize_processed_hls",
        fake_materialize_processed_hls,
        raising=True,
    )

    update_fields = streamable_media.sync_video_streamable_artifacts(
        cast(VideoFile, video),
        include_raw=False,
        include_processed=True,
        save=True,
        force=True,
    )

    assert update_fields == []
    assert calls == [(cast(VideoFile, video), True)]
