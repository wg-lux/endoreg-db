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


@pytest.mark.unit
def test_transcode_video_force_cpu_uses_cpu_only_flags(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")

    captured = {}

    def fake_get_preferred_encoder():
        return {
            "name": "h264_nvenc",
            "preset_param": "-preset",
            "preset_value": "p4",
            "quality_param": "-cq",
            "quality_value": "20",
            "type": "nvenc",
            "fallback_preset": "p1",
        }

    def fake_run(command, **kwargs):
        captured["command"] = command
        output_path.write_bytes(b"encoded")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper._get_preferred_encoder",
        fake_get_preferred_encoder,
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = transcode_video(input_path, output_path, force_cpu=True)

    assert result == output_path
    assert captured["command"][0] == "ffmpeg"
    assert "-c:v" in captured["command"]
    codec_index = captured["command"].index("-c:v")
    assert captured["command"][codec_index + 1] == "libx264"
    assert "-crf" in captured["command"]
    assert "-cq" not in captured["command"]
    assert "-gpu" not in captured["command"]
    assert "-rc" not in captured["command"]
