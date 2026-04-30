import subprocess

import pytest

from endoreg_db.utils.video import ffmpeg_wrapper
from endoreg_db.utils.video.ffmpeg_wrapper import _build_encoder_args, transcode_video


class FakePopen:
    def __init__(
        self,
        command,
        *,
        returncode=0,
        stderr_output="",
        timeout=False,
        **kwargs,
    ):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr_output
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
    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

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
    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

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
    assert captured["command"][0] == "/smart/bin/ffmpeg"
    assert "-c:v" in captured["command"]
    codec_index = captured["command"].index("-c:v")
    assert captured["command"][codec_index + 1] == "libx264"
    assert "-crf" in captured["command"]
    assert "-cq" not in captured["command"]
    assert "-gpu" not in captured["command"]
    assert "-rc" not in captured["command"]


@pytest.mark.unit
def test_transcode_video_retries_timestamp_repair(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")

    commands = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            return FakePopen(
                command,
                returncode=1,
                stderr_output="invalid dts: timestamp too large and out of range",
                **kwargs,
            )
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = transcode_video(input_path, output_path, force_cpu=True)

    assert result == output_path
    assert len(commands) == 2
    assert "-fflags" not in commands[0]
    assert "-fflags" in commands[1]
    assert commands[1][commands[1].index("-fflags") + 1] == "+genpts"


@pytest.mark.unit
def test_create_sensitive_copy_fails_when_video_transcode_fails(
    monkeypatch,
    tmp_path,
):
    from endoreg_db.import_files.file_storage.storage import create_sensitive_copy

    input_path = tmp_path / "input.mp4"
    sensitive_root = tmp_path / "sensitive"
    input_path.write_bytes(b"input")

    monkeypatch.setattr(
        "endoreg_db.import_files.file_storage.storage.transcode_video",
        lambda src, dest: None,
    )

    with pytest.raises(RuntimeError, match="Video transcode failed"):
        create_sensitive_copy(
            input_path,
            sensitive_root,
            type("Ctx", (), {"file_type": "video"})(),
        )


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


@pytest.mark.unit
def test_ffmpeg_timestamp_fault_detection_requires_timestamp_and_fault_signal():
    assert ffmpeg_wrapper._stderr_indicates_timestamp_fault(
        "Non monotonically increasing DTS in output stream"
    )
    assert ffmpeg_wrapper._stderr_indicates_timestamp_fault(
        "PTS timestamp too large and out of range"
    )
    assert not ffmpeg_wrapper._stderr_indicates_timestamp_fault(
        "Encoder failed without timing detail"
    )
    assert not ffmpeg_wrapper._stderr_indicates_timestamp_fault("")


@pytest.mark.unit
def test_update_or_append_ffmpeg_arg_replaces_appends_and_repairs_missing_value():
    args = ["-pix_fmt", "yuvj420p"]

    ffmpeg_wrapper._update_or_append_ffmpeg_arg(args, "-pix_fmt", "yuv420p")
    ffmpeg_wrapper._update_or_append_ffmpeg_arg(args, "-color_range", "pc")
    ffmpeg_wrapper._update_or_append_ffmpeg_arg(args, "-movflags", "faststart")

    assert args == [
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "pc",
        "-movflags",
        "faststart",
    ]


@pytest.mark.unit
def test_extract_frame_range_numbers_outputs_by_requested_frame(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_dir = tmp_path / "frames"
    input_path.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(
        "endoreg_db.utils.video.ffmpeg_wrapper._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        output_dir.mkdir(parents=True, exist_ok=True)
        for frame_number in range(10, 13):
            (output_dir / f"frame_{frame_number:07d}.jpg").write_bytes(b"frame")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ffmpeg_wrapper.extract_frame_range(
        input_path,
        output_dir,
        start_frame=10,
        end_frame=13,
        quality=2,
    )

    assert [path.name for path in result] == [
        "frame_0000010.jpg",
        "frame_0000011.jpg",
        "frame_0000012.jpg",
    ]
    assert "-start_number" in captured["command"]
    assert captured["command"][captured["command"].index("-start_number") + 1] == "10"
