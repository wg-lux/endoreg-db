from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from endoreg_db.services import video_transcoding


@pytest.mark.unit
def test_transcode_video_command_transcodes_input_dir(monkeypatch, tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")

    def fake_transcode_video(
        input_path: Path,
        output_path: Path,
        **kwargs,
    ) -> Path:
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
        lambda path: SimpleNamespace(compliant=True, reasons=[], error=""),
    )
    monkeypatch.setattr(
        video_transcoding.ffmpeg_wrapper,
        "get_stream_info",
        lambda path: {
            "streams": [
                {
                    "codec_type": "video",
                    "avg_frame_rate": "50/1",
                }
            ]
        },
    )

    stdout = StringIO()
    call_command(
        "transcode_video",
        "--input-dir",
        str(input_dir),
        "--output-dir",
        str(output_dir),
        "--allow-unmanaged-output",
        "--json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["scanned_files"] == 1
    assert payload["transcoded_files"] == 1
    assert payload["failed_files"] == 0
    assert payload["target_fps"] == 50.0
    assert (output_dir / "clip.mp4").read_bytes() == b"standardized-video"


@pytest.mark.unit
def test_transcode_video_command_rejects_missing_input_dir(tmp_path):
    with pytest.raises(CommandError, match="input_dir does not exist"):
        call_command(
            "transcode_video",
            "--input-dir",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "output"),
            "--allow-unmanaged-output",
        )


@pytest.mark.unit
def test_transcode_video_command_requires_managed_output_by_default(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "clip.mp4").write_bytes(b"raw-video")

    with pytest.raises(CommandError, match="output_dir must be inside"):
        call_command(
            "transcode_video",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "output"),
        )
