import subprocess

import pytest

from endoreg_db.utils.video.ffmpeg_wrapper import transcode_video


@pytest.mark.unit
def test_transcode_video_timeout_removes_partial_output(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"partial")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args", args[0]), timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = transcode_video(input_path, output_path, force_cpu=True)

    assert result is None
    assert not output_path.exists()
