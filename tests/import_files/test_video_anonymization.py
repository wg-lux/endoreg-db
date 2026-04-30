import os

import pytest

from endoreg_db.import_files.processing.video_processing import video_anonymization


@pytest.mark.unit
def test_ensure_ffmpeg_tools_on_path_prepends_resolved_tool_dir(monkeypatch, tmp_path):
    tool_dir = tmp_path / "ffmpeg-bin"
    tool_dir.mkdir()
    ffmpeg = tool_dir / "ffmpeg"
    ffprobe = tool_dir / "ffprobe"
    ffmpeg.write_text("#!/bin/sh\n", encoding="utf-8")
    ffprobe.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffmpeg_executable",
        lambda: str(ffmpeg),
    )
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffprobe_executable",
        lambda: str(ffprobe),
    )

    video_anonymization._ensure_ffmpeg_tools_on_path()

    path_parts = os.environ["PATH"].split(os.pathsep)
    assert path_parts[0] == tool_dir.as_posix()
    assert "/usr/bin" in path_parts


@pytest.mark.unit
def test_ensure_ffmpeg_tools_on_path_errors_when_tools_missing(monkeypatch):
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffmpeg_executable",
        lambda: None,
    )
    monkeypatch.setattr(
        video_anonymization,
        "_resolve_ffprobe_executable",
        lambda: "/smart/bin/ffprobe",
    )

    with pytest.raises(RuntimeError, match="FFmpeg and ffprobe are required"):
        video_anonymization._ensure_ffmpeg_tools_on_path()
