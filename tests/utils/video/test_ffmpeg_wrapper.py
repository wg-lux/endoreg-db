import subprocess

import pytest

from endoreg_db.utils.video.ffmpeg_wrapper import _build_encoder_args, transcode_video


class FakePopen:
    def __init__(self, command, *, returncode=0, stderr="", timeout=False, **kwargs):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.timeout = timeout
        self.killed = False
        self.kwargs = kwargs

    def communicate(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(cmd=self.command, timeout=timeout)
        return "", self.stderr

    def kill(self):
        self.killed = True


@pytest.mark.unit
def test_transcode_video_timeout_removes_partial_output(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"partial")

    created_processes = []

    def fake_popen(command, **kwargs):
        process = FakePopen(command, timeout=True, **kwargs)
        created_processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = transcode_video(input_path, output_path, force_cpu=True)

    assert result is None
    assert not output_path.exists()
    assert created_processes
    assert created_processes[0].killed


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

    def fake_popen(command, **kwargs):
        captured["command"] = command
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0, **kwargs)

    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper._get_preferred_encoder",
        fake_get_preferred_encoder,
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

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


@pytest.mark.unit
def test_build_encoder_args_nvenc_forces_yuv420p_format(monkeypatch):
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

    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper._get_preferred_encoder",
        fake_get_preferred_encoder,
    )

    encoder_args, encoder_type = _build_encoder_args()

    assert encoder_type == "nvenc"
    assert "-vf" in encoder_args
    assert encoder_args[encoder_args.index("-vf") + 1] == "format=yuv420p"
