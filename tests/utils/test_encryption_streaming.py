from pathlib import Path
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
import hashlib
from typing import NoReturn

import pytest

from endoreg_db.services import streamable_media as sm
from endoreg_db.utils.storage_profile import PayloadKind, StoragePolicy


class DummyStorageMode:
    STREAMABLE = "fs_encrypted_streamable"
    ENCRYPTED = "app_encrypted"


@dataclass(frozen=True)
class DummyFieldFile:
    name: str


@dataclass(frozen=True)
class StreamableRoots:
    root: Path
    raw_root: Path
    processed_root: Path


class DummyVideo:
    StorageMode = DummyStorageMode

    def __init__(self) -> None:
        self.raw_payload = b"\x00\x00\x00\x20ftypmp42raw-source"
        self.processed_payload = b"\x00\x00\x00\x20ftypmp42processed-source"
        self.raw_streamable_payload = b"\x00\x00\x00\x20ftypmp42moovmdatraw"
        self.processed_streamable_payload = b"\x00\x00\x00\x20ftypmp42moovmdatprocessed"
        self.pk = 1
        self.video_hash = hashlib.sha256(self.raw_payload).hexdigest()
        self.processed_video_hash = hashlib.sha256(self.processed_payload).hexdigest()
        self.raw_file = DummyFieldFile("raw/input.mp4")
        self.processed_file = DummyFieldFile("processed/input.mp4")
        self.raw_streamable_relative_path = ""
        self.processed_streamable_relative_path = ""
        self.storage_mode = DummyStorageMode.ENCRYPTED
        self.saved_update_fields: list[str] = []

    def save(self, update_fields: Iterable[str] = ()) -> None:
        self.saved_update_fields = list(update_fields or [])


def _root_provider(path: Path) -> Callable[[], Path]:
    def provide_path() -> Path:
        return path

    return provide_path


def _relative_path_provider(root: Path) -> Callable[[Path], str]:
    def relative_path(path: Path) -> str:
        return Path(path).relative_to(root).as_posix()

    return relative_path


def _fs_streamable_policy(kind: PayloadKind) -> StoragePolicy:
    return StoragePolicy.FS_STREAMABLE


def _app_encrypted_policy(kind: PayloadKind) -> StoragePolicy:
    return StoragePolicy.APP_ENCRYPTED


def _constant_field_file_size(size: int) -> Callable[[DummyFieldFile], int]:
    def field_file_size(field_file: DummyFieldFile) -> int:
        return size

    return field_file_size


def _fail_field_file_size(field_file: DummyFieldFile) -> NoReturn:
    raise AssertionError("should use local source path size")


def _field_file_payload_reader(
    payload: bytes,
) -> Callable[
    [DummyFieldFile, int, int],
    Iterator[bytes],
]:
    def iter_field_file_bytes(
        field_file: DummyFieldFile,
        start: int,
        end: int,
    ) -> Iterator[bytes]:
        return iter([payload])

    return iter_field_file_bytes


def _encrypted_payload_reader(
    field_file: DummyFieldFile,
    start: int,
    end: int,
) -> Iterator[bytes]:
    return iter([sm.LX_ENCRYPTED_MAGIC, b"ciphertext"])


def _fake_streamable_transcode(payload: bytes) -> Callable[..., Path]:
    def transcode_streamable_mp4(
        source_path: Path, target_path: Path, **_: object
    ) -> Path:
        assert source_path.exists()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(payload)
        return target_path

    return transcode_streamable_mp4


def _fail_materialize_streamable_target(
    video_field_file: DummyFieldFile,
    target_path: Path,
    *,
    source_path: Path | None = None,
    expected_hash: str = "",
) -> NoReturn:
    raise AssertionError("should not rematerialize existing plaintext streamable file")


def _fail_rewrite_streamable_target(
    video_field_file: DummyFieldFile,
    target_path: Path,
    *,
    source_path: Path | None = None,
    expected_hash: str = "",
) -> NoReturn:
    raise AssertionError("should not rewrite existing plaintext file")


def _fail_source_hash(
    video_field_file: DummyFieldFile,
    source_path: Path | None,
) -> NoReturn:
    raise AssertionError(
        "should not hash source for an already recorded streamable path"
    )


@pytest.fixture
def video() -> DummyVideo:
    return DummyVideo()


@pytest.fixture
def streamable_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> StreamableRoots:
    raw_root = tmp_path / "streamable_videos" / "raw"
    processed_root = tmp_path / "streamable_videos" / "processed"

    monkeypatch.setattr(sm, "_streamable_raw_video_root", _root_provider(raw_root))
    monkeypatch.setattr(
        sm,
        "_streamable_processed_video_root",
        _root_provider(processed_root),
    )

    monkeypatch.setattr(
        sm,
        "_streamable_relative_path",
        _relative_path_provider(tmp_path),
    )

    return StreamableRoots(
        root=tmp_path,
        raw_root=raw_root,
        processed_root=processed_root,
    )


def test_materialize_refuses_encrypted_streamable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "bad.mp4"

    monkeypatch.setattr(sm, "field_file_size", _constant_field_file_size(8))
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        _encrypted_payload_reader,
    )

    with pytest.raises(RuntimeError, match="Refusing encrypted streamable source"):
        sm._materialize_streamable_target(DummyFieldFile("raw/input.mp4"), target)
    assert not target.exists()


def test_source_size_prefers_existing_local_source_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"local source bytes")
    monkeypatch.setattr(sm, "field_file_size", _fail_field_file_size)

    assert sm._source_size(DummyFieldFile("raw/input.mp4"), source_path) == len(
        b"local source bytes"
    )


def test_streamable_transcode_uses_small_faststart_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.mp4"
    target = tmp_path / "target.mp4"
    source.write_bytes(b"\x00\x00\x00\x20ftypmp42moovmdatsource")
    captured: dict[str, object] = {}

    def fake_transcode_video(
        input_path: Path, output_path: Path, **kwargs: object
    ) -> Path:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"\x00\x00\x00\x20ftypmp42moovmdatproxy")
        return output_path

    monkeypatch.setattr(sm.ffmpeg_wrapper, "transcode_video", fake_transcode_video)

    result = sm._transcode_streamable_mp4(source, target)

    assert result == target
    assert target.read_bytes() == b"\x00\x00\x00\x20ftypmp42moovmdatproxy"
    assert captured["input_path"] == source
    assert captured["output_path"] != target
    assert captured["kwargs"] == {
        "codec": "libx264",
        "crf": 35,
        "preset": "veryfast",
        "audio_codec": "aac",
        "audio_bitrate": "32k",
        "extra_args": [
            "-profile:v",
            "high",
            "-vf",
            "scale=-2:480:in_range=auto:out_range=full,format=yuv420p",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "pc",
            "-fpsmax",
            "50",
            "-movflags",
            "+faststart",
            "-an",
        ],
        "quality_mode": "fast",
        "force_cpu": True,
    }


def test_streamable_media_state_centralizes_artifact_decisions(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def policy(kind: PayloadKind) -> StoragePolicy:
        if kind == PayloadKind.VIDEO_RAW:
            return StoragePolicy.FS_STREAMABLE
        return StoragePolicy.APP_ENCRYPTED

    video.processed_streamable_relative_path = (
        f"streamable_videos/processed/{video.processed_video_hash}.mp4"
    )
    monkeypatch.setattr(sm, "resolve_storage_policy", policy)

    state = sm.resolve_streamable_media_state(
        video,
        include_raw=True,
        include_processed=True,
    )
    decisions = {decision.spec.kind: decision for decision in state.artifacts}

    raw_decision = decisions[sm.StreamableArtifactKind.RAW]
    processed_decision = decisions[sm.StreamableArtifactKind.PROCESSED]

    assert raw_decision.disposition == sm.StreamableArtifactDisposition.SYNC
    assert raw_decision.target_path == streamable_roots.raw_root / (
        f"{video.video_hash}.mp4"
    )
    assert (
        processed_decision.disposition
        == sm.StreamableArtifactDisposition.CLEAR_STALE_PATH
    )
    assert processed_decision.target_path is None


def test_sync_does_not_materialize_plaintext_raw_or_set_streamable_mode(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )
    monkeypatch.setattr(sm, "field_file_size", _constant_field_file_size(12))
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        _field_file_payload_reader(video.raw_payload),
    )
    monkeypatch.setattr(
        sm,
        "_transcode_streamable_mp4",
        _fake_streamable_transcode(video.raw_streamable_payload),
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"

    assert not target.exists()
    assert video.raw_streamable_relative_path == ""
    assert video.storage_mode == DummyStorageMode.ENCRYPTED
    assert update_fields == []
    assert video.saved_update_fields == []


def test_sync_is_idempotent_and_does_not_rewrite_existing_plaintext(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )
    monkeypatch.setattr(
        sm,
        "field_file_size",
        _constant_field_file_size(len(video.raw_payload)),
    )
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        _field_file_payload_reader(video.raw_payload),
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.raw_streamable_payload)
    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.storage_mode = DummyStorageMode.STREAMABLE

    monkeypatch.setattr(
        sm,
        "_materialize_streamable_target",
        _fail_materialize_streamable_target,
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert not target.exists()
    assert video.raw_streamable_relative_path == ""
    assert video.storage_mode == DummyStorageMode.ENCRYPTED
    assert update_fields == ["raw_streamable_relative_path", "storage_mode"]


def test_sync_force_removes_existing_plaintext(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )
    monkeypatch.setattr(
        sm,
        "field_file_size",
        _constant_field_file_size(len(video.raw_payload)),
    )
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        _field_file_payload_reader(video.raw_payload),
    )
    monkeypatch.setattr(
        sm,
        "_transcode_streamable_mp4",
        _fake_streamable_transcode(video.raw_streamable_payload),
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.raw_streamable_payload)

    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.storage_mode = DummyStorageMode.STREAMABLE

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
        force=True,
    )

    assert not target.exists()
    assert video.raw_streamable_relative_path == ""
    assert video.storage_mode == DummyStorageMode.ENCRYPTED
    assert update_fields == ["raw_streamable_relative_path", "storage_mode"]


def test_sync_idempotent_path_does_not_rehash_source(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )
    monkeypatch.setattr(sm, "_source_hash", _fail_source_hash)

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.raw_streamable_payload)

    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.storage_mode = DummyStorageMode.STREAMABLE

    monkeypatch.setattr(
        sm,
        "_materialize_streamable_target",
        _fail_materialize_streamable_target,
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert not target.exists()
    assert update_fields == ["raw_streamable_relative_path", "storage_mode"]


def test_sync_does_not_adopt_untracked_existing_plaintext(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )
    monkeypatch.setattr(
        sm,
        "field_file_size",
        _constant_field_file_size(len(video.raw_payload)),
    )
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        _field_file_payload_reader(video.raw_payload),
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(video.raw_streamable_payload)

    video.raw_streamable_relative_path = ""

    monkeypatch.setattr(
        sm,
        "_materialize_streamable_target",
        _fail_rewrite_streamable_target,
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert video.raw_streamable_relative_path == ""
    assert target.exists()
    assert update_fields == []


def test_sync_removes_existing_plaintext_with_wrong_hash(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )
    monkeypatch.setattr(
        sm,
        "field_file_size",
        _constant_field_file_size(len(video.raw_payload)),
    )
    monkeypatch.setattr(
        sm,
        "iter_field_file_bytes",
        _field_file_payload_reader(video.raw_payload),
    )
    monkeypatch.setattr(
        sm,
        "_transcode_streamable_mp4",
        _fake_streamable_transcode(video.raw_streamable_payload),
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x00\x00\x00\x20ftypmp42mdatwrongmoov")
    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.storage_mode = DummyStorageMode.STREAMABLE

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=True,
    )

    assert not target.exists()
    assert video.raw_streamable_relative_path == ""
    assert video.storage_mode == DummyStorageMode.ENCRYPTED
    assert update_fields == ["raw_streamable_relative_path", "storage_mode"]


def test_dry_run_does_not_write_file_or_set_streamable_mode(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _fs_streamable_policy,
    )

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=False,
        save=False,
    )

    target = streamable_roots.raw_root / f"{video.video_hash}.mp4"

    assert not target.exists()
    assert video.saved_update_fields == []
    assert video.storage_mode == DummyStorageMode.ENCRYPTED
    assert "storage_mode" not in update_fields


def test_skipped_policy_clears_stale_streamable_paths_and_app_encrypted_mode(
    video: DummyVideo,
    streamable_roots: StreamableRoots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sm,
        "resolve_storage_policy",
        _app_encrypted_policy,
    )

    video.raw_streamable_relative_path = f"streamable_videos/raw/{video.video_hash}.mp4"
    video.processed_streamable_relative_path = (
        f"streamable_videos/processed/{video.processed_video_hash}.mp4"
    )
    video.storage_mode = DummyStorageMode.STREAMABLE

    update_fields = sm.sync_video_streamable_artifacts(
        video,
        include_raw=True,
        include_processed=True,
        save=True,
    )

    assert video.raw_streamable_relative_path == ""
    assert video.processed_streamable_relative_path == ""
    assert video.storage_mode == DummyStorageMode.ENCRYPTED
    assert "raw_streamable_relative_path" in update_fields
    assert "processed_streamable_relative_path" in update_fields
    assert "storage_mode" in update_fields
