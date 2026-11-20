# pyright: reportPrivateUsage=false
from __future__ import annotations

from io import BytesIO
from os import stat_result
from pathlib import Path
from unittest.mock import Mock

import pytest

from endoreg_db.services.streaming import hls_media as hls_media
from endoreg_db.services.video_storage.hls_encoding import (
    HlsEncodingProfileName,
    hls_encoding_profile_by_name,
)


@pytest.mark.no_db
@pytest.mark.parametrize("size", [0, 17, 1024 * 1024 + 17])
def test_source_staging_uses_bounded_reads_and_one_capacity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int
) -> None:
    from endoreg_db.utils import file_operations

    payload = b"x" * size
    source = BytesIO(payload)
    read = Mock(wraps=source.read)
    monkeypatch.setattr(source, "read", read)
    capacity = Mock(wraps=file_operations.ensure_disk_capacity)
    monkeypatch.setattr(file_operations, "ensure_disk_capacity", capacity)

    output = hls_media._materialize_seekable_plaintext_source(
        source=source,
        source_file_name="source.mp4",
        source_size_bytes=size,
        temp_source_dir=tmp_path,
    )

    assert output.read_bytes() == payload
    assert all(
        call.args == (hls_media.HLS_SOURCE_CHUNK_BYTES,) for call in read.call_args_list
    )
    capacity.assert_called_once_with(destination_dir=tmp_path, required_bytes=size)


@pytest.mark.no_db
def test_output_progress_stats_each_candidate_once_and_ignores_nonfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    segment = tmp_path / "seg_000.ts"
    segment.write_bytes(b"segment")
    directory = tmp_path / "seg_directory.ts"
    directory.mkdir()
    playlist = tmp_path / "playlist.m3u8"  # Not yet published by FFmpeg.
    original_stat = Path.stat
    calls: dict[Path, int] = {}

    def counted_stat(path: Path, *, follow_symlinks: bool = True) -> stat_result:
        calls[path] = calls.get(path, 0) + 1
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", counted_stat)
    progress = hls_media._hls_output_progress(
        segment_dir=tmp_path, playlist_path=playlist
    )

    assert progress.file_count == 1
    assert progress.total_bytes == len(b"segment")
    assert progress.latest_mtime_ns > 0
    assert all(calls[path] == 1 for path in (segment, directory, playlist))


@pytest.mark.parametrize("ffmpeg_available", [False, True])
def test_encoder_failure_does_not_read_or_stage_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ffmpeg_available: bool,
) -> None:
    encoder_error = hls_media.VideoStorageNormalizationError("encoder unavailable")
    preflight = Mock(side_effect=encoder_error)
    materialize = Mock()
    probe = Mock()
    monkeypatch.setattr(
        hls_media.ffmpeg_wrapper,
        "resolve_ffmpeg_executable",
        lambda: "/usr/bin/ffmpeg" if ffmpeg_available else None,
    )
    monkeypatch.setattr(hls_media, "assert_hls_encoder_runtime_available", preflight)
    monkeypatch.setattr(
        hls_media, "_materialize_seekable_plaintext_source", materialize
    )
    monkeypatch.setattr(hls_media, "probe_video_artifact", probe)

    # A closed stream raises on any read, independently of the encoder failure.
    source = BytesIO()
    source.close()
    with pytest.raises(
        hls_media.VideoStorageNormalizationError if ffmpeg_available else RuntimeError,
        match="encoder unavailable" if ffmpeg_available else "ffmpeg executable",
    ) as caught:
        _run(source=source, directory=tmp_path)

    if ffmpeg_available:
        assert caught.value is encoder_error
        preflight.assert_called_once()
    else:
        preflight.assert_not_called()
    materialize.assert_not_called()
    probe.assert_not_called()
    assert not (tmp_path / "source").exists()


def test_successful_preflight_preserves_source_failure_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    preflight = Mock()
    cleanup = Mock()
    monkeypatch.setattr(
        hls_media.ffmpeg_wrapper, "resolve_ffmpeg_executable", lambda: "/usr/bin/ffmpeg"
    )
    monkeypatch.setattr(hls_media, "assert_hls_encoder_runtime_available", preflight)
    monkeypatch.setattr(hls_media, "_cleanup_seekable_plaintext_source", cleanup)
    source = BytesIO()
    source.close()

    with pytest.raises(ValueError, match="closed file"):
        _run(source=source, directory=tmp_path)

    preflight.assert_called_once()
    cleanup.assert_called_once_with(
        temp_source_dir=tmp_path / "source", source_path=None
    )


def _run(*, source: BytesIO, directory: Path) -> None:
    hls_media._run_ffmpeg_hls(
        source=source,
        source_file_name="source.mp4",
        source_size_bytes=None,
        temp_source_dir=directory / "source",
        key_info_path=directory / "key-info",
        segment_pattern=directory / "seg_%05d.ts",
        playlist_path=directory / "playlist.m3u8",
        segment_base_url="/segments/",
        encoding_profile=hls_encoding_profile_by_name(
            HlsEncodingProfileName.CLINICAL_H264_NVENC_CQ_V1
        ),
    )
