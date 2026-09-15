from __future__ import annotations

# pyright: reportPrivateUsage=false

import importlib
import stat
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, cast

from pytest import MonkeyPatch

from endoreg_db.utils.media.frame_file_permissions import (
    FRAME_CACHE_DIR_MODE,
    FRAME_FILE_MODE,
    FRAME_STAGING_DIR_MODE,
)

extract_frames_module = importlib.import_module(
    "endoreg_db.services.video_files._frames._extract_frames"
)
range_module = importlib.import_module(
    "endoreg_db.services.video_files._frames._manage_frame_range"
)


class _FakeVideo:
    video_hash = "permission-video"
    has_raw = True
    raw_file = None
    processed_file = None


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _source_context(source_path: Path):
    @contextmanager
    def _ctx() -> Generator[Path]:
        yield source_path

    return _ctx()


def _source_context_factory(
    source_path: Path,
) -> AbstractContextManager[Path]:
    return _source_context(source_path)


def test_full_frame_extraction_restricts_staged_frame_permissions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    output_dir = tmp_path / "staged-full"
    video = _FakeVideo()

    def _fake_source_context(
        _video: object, *, from_processed: bool
    ) -> AbstractContextManager[Path]:
        return _source_context_factory(source_path)

    monkeypatch.setattr(
        extract_frames_module,
        "_video_source_context",
        _fake_source_context,
    )

    def _fake_extract_frames(
        _video_path: Path,
        target_dir: Path,
        *,
        quality: int,
        ext: str,
    ) -> list[Path]:
        frame_path = target_dir / f"frame_{0:07d}.{ext}"
        frame_path.write_bytes(b"jpeg")
        frame_path.chmod(0o666)
        return [frame_path]

    monkeypatch.setattr(
        extract_frames_module,
        "ffmpeg_extract_frames",
        _fake_extract_frames,
    )

    extract_full_frame_set_to_directory = cast(
        Callable[..., list[Path]],
        getattr(extract_frames_module, "extract_full_frame_set_to_directory"),
    )
    frame_paths = extract_full_frame_set_to_directory(
        cast(Any, video),
        output_dir=output_dir,
    )

    assert _mode(output_dir) == FRAME_STAGING_DIR_MODE
    assert [_mode(path) for path in frame_paths] == [FRAME_FILE_MODE]


def test_range_frame_extraction_installs_restricted_cache_permissions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"video")
    frame_dir = tmp_path / "frames"
    video = _FakeVideo()

    def _fake_source_context(
        _video: object, *, from_processed: bool
    ) -> AbstractContextManager[Path]:
        return _source_context_factory(source_path)

    monkeypatch.setattr(
        range_module,
        "_video_source_context",
        _fake_source_context,
    )

    def _fake_extract_frame_range(
        _video_path: Path,
        target_dir: Path,
        start_frame: int,
        end_frame: int,
        *,
        quality: int,
        ext: str,
    ) -> list[Path]:
        paths: list[Path] = []
        for frame_number in range(start_frame, end_frame):
            frame_path = target_dir / f"frame_{frame_number:07d}.{ext}"
            frame_path.write_bytes(b"jpeg")
            frame_path.chmod(0o666)
            paths.append(frame_path)
        return paths

    monkeypatch.setattr(
        range_module,
        "ffmpeg_extract_frame_range",
        _fake_extract_frame_range,
    )

    extract_frame_range_to_directory = cast(
        Callable[..., list[Path]],
        getattr(range_module, "extract_frame_range_to_directory"),
    )
    frame_paths = extract_frame_range_to_directory(
        cast(Any, video),
        output_dir=frame_dir,
        start_frame=2,
        end_frame=4,
    )

    assert _mode(frame_dir) == FRAME_CACHE_DIR_MODE
    assert [_mode(path) for path in frame_paths] == [FRAME_FILE_MODE, FRAME_FILE_MODE]
    assert not any(
        path.name.startswith(".range_extract_") for path in tmp_path.iterdir()
    )
