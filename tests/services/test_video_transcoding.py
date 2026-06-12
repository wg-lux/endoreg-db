from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pytest import MonkeyPatch

from endoreg_db.services import video_transcoding


def _fake_compliant_video_format(path: Path) -> SimpleNamespace:
    return SimpleNamespace(compliant=True, reasons=[], error="")


def _fake_50_fps_stream_info(path: Path) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "50/1",
            }
        ]
    }


def _fake_30000_1001_stream_info(path: Path) -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "avg_frame_rate": "30000/1001",
            }
        ]
    }



@pytest.mark.unit
def test_transcode_video_directory_stages_and_moves_output(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    source = input_dir / "clip.avi"
    source.write_bytes(b"raw-video")

    captured: dict[str, object] = {}

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> Path:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"standardized-video")
        return output_path

    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "transcode_video",
        fake_transcode_video,
    )
    monkeypatch.setattr(
        video_transcoding,
        "classify_video_format",
        _fake_compliant_video_format,
    )
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "get_stream_info",
        _fake_50_fps_stream_info,
    )

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
        force_cpu=True,
        quality_mode="quality",
    )

    destination = output_dir / "clip.mp4"
    assert summary.scanned_files == 1
    assert summary.transcoded_files == 1
    assert summary.failed_files == 0
    assert summary.target_fps == 50.0
    assert destination.read_bytes() == b"standardized-video"
    assert captured["input_path"] == source.resolve()
    assert captured["output_path"] != destination
    assert captured["kwargs"] == {
        "extra_args": ["-pix_fmt", "yuv420p", "-color_range", "pc", "-r", "50"],
        "quality_mode": "quality",
        "force_cpu": True,
    }


@pytest.mark.unit
def test_transcode_video_directory_dry_run_does_not_create_output_dir(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
        dry_run=True,
    )

    assert summary.scanned_files == 1
    assert summary.planned_files == 1
    assert summary.transcoded_files == 0
    assert not output_dir.exists()
    assert summary.reports[0].destination == str((output_dir / "clip.mp4").resolve())


@pytest.mark.unit
def test_transcode_video_directory_uses_configured_target_fps(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")

    captured: dict[str, object] = {}

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> Path:
        captured["kwargs"] = kwargs
        output_path.write_bytes(b"standardized-video")
        return output_path

    monkeypatch.setattr(video_transcoding, "_default_target_fps", lambda: 29.97)
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "transcode_video",
        fake_transcode_video,
    )
    monkeypatch.setattr(
        video_transcoding,
        "classify_video_format",
        _fake_compliant_video_format,
    )
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "get_stream_info",
        _fake_30000_1001_stream_info,
    )

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
    )

    assert summary.transcoded_files == 1
    assert summary.target_fps == 29.97
    assert captured["kwargs"] == {
        "extra_args": ["-pix_fmt", "yuv420p", "-color_range", "pc", "-r", "29.97"],
        "quality_mode": "balanced",
        "force_cpu": False,
    }


@pytest.mark.unit
def test_transcode_video_directory_skips_existing_destination(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")
    destination = output_dir / "clip.mp4"
    destination.write_bytes(b"existing-video")

    summary = video_transcoding.transcode_video_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        allow_unmanaged_output=True,
    )

    assert summary.skipped_files == 1
    assert summary.transcoded_files == 0
    assert destination.read_bytes() == b"existing-video"
    assert (
        summary.reports[0].action
        == video_transcoding.VideoTranscodeAction.SKIP_EXISTING
    )
