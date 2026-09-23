# pyright: reportPrivateUsage=false
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from endoreg_db.services import hls_media
from endoreg_db.services.video_storage.hls_encoding import (
    HlsEncodingProfileName,
    hls_encoding_profile_by_name,
)


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
        timeline_validation=hls_media._HlsTimelineValidation(
            proof=None,
            expected_content_hash=None,
            source_generation_id=uuid4(),
            source_content_hash="",
        ),
        encoding_profile=hls_encoding_profile_by_name(
            HlsEncodingProfileName.CLINICAL_H264_NVENC_CQ_V1
        ),
    )
