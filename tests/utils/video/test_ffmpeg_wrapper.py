import subprocess
from pathlib import Path

import pytest

from endoreg_db.utils.video import ffmpeg_wrapper
from endoreg_db.utils.video.command_construction import (
    TimestampRepairMode,
    _build_extract_frame_range_command,
    _build_extract_frames_command,
    _build_filter_transcode_command,
    _build_transcode_command,
)
from endoreg_db.utils.video import transcode_execution
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


@pytest.fixture(autouse=True)
def _valid_transcode_output_stream(monkeypatch):
    monkeypatch.setattr(
        transcode_execution,
        "get_stream_info",
        lambda _path: {"streams": [{"codec_type": "video", "codec_name": "h264"}]},
    )


@pytest.mark.unit
def test_transcode_video_timeout_removes_partial_output(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"partial")
    monkeypatch.setattr(
        "endoreg_db.utils.video.transcode_execution._resolve_ffmpeg_executable",
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
        "endoreg_db.utils.video.transcode_execution._resolve_ffmpeg_executable",
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
        "endoreg_db.utils.video.encoder_policy._get_preferred_encoder",
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
    assert captured["command"] == [
        "/smart/bin/ffmpeg",
        "-i",
        str(input_path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-y",
        str(output_path),
    ]


@pytest.mark.unit
def test_transcode_output_validation_rejects_non_video_payload(monkeypatch, tmp_path):
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(b"not empty but not video")
    monkeypatch.setattr(
        transcode_execution,
        "get_stream_info",
        lambda _path: {"streams": []},
    )

    assert ffmpeg_wrapper._transcode_output_is_valid(output_path) is False


@pytest.mark.unit
def test_transcode_video_retries_timestamp_repair(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    monkeypatch.setattr(
        "endoreg_db.utils.video.transcode_execution._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

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
        "endoreg_db.import_files.file_storage.storage.transcode_videofile_if_required",
        lambda src, dest: None,
    )

    with pytest.raises(RuntimeError, match="Video transcode failed"):
        create_sensitive_copy(
            input_path,
            sensitive_root,
            type("ImportContext", (), {"file_type": "video"})(),
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
        "endoreg_db.utils.video.encoder_policy._get_preferred_encoder",
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
    args = ["-pix_fmt", "yuv420p"]

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
def test_build_transcode_command_preserves_legacy_extra_arg_order():
    command = _build_transcode_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        input_path=Path("/data/input.mp4"),
        output_path=Path("/data/output.mp4"),
        encoder_args=[
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-profile:v",
            "high",
        ],
        audio_codec="aac",
        audio_bitrate="128k",
        extra_args=["-pix_fmt", "yuv420p", "-color_range", "pc"],
        timestamp_repair_mode=TimestampRepairMode.NONE,
    )

    assert command == [
        "/smart/bin/ffmpeg",
        "-i",
        "/data/input.mp4",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-y",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "pc",
        "/data/output.mp4",
    ]


@pytest.mark.unit
def test_build_transcode_command_preserves_legacy_timestamp_repair_order():
    command = _build_transcode_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        input_path=Path("/data/input.mp4"),
        output_path=Path("/data/output.mp4"),
        encoder_args=["-c:v", "libx264"],
        audio_codec="aac",
        audio_bitrate="128k",
        extra_args=None,
        timestamp_repair_mode=TimestampRepairMode.RESET_TO_ZERO,
    )

    assert command == [
        "/smart/bin/ffmpeg",
        "-fflags",
        "+genpts+igndts",
        "-err_detect",
        "ignore_err",
        "-i",
        "/data/input.mp4",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-avoid_negative_ts",
        "make_zero",
        "-muxdelay",
        "0",
        "-y",
        "/data/output.mp4",
    ]


@pytest.mark.unit
def test_build_frame_extraction_commands_preserve_legacy_order():
    output_pattern = Path("/data/frames/frame_%07d.jpg")

    full_command = _build_extract_frames_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        video_path=Path("/data/input.mp4"),
        output_pattern=output_pattern,
        quality=2,
        fps=5.0,
    )
    range_command = _build_extract_frame_range_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        video_path=Path("/data/input.mp4"),
        output_pattern=output_pattern,
        start_frame=10,
        end_frame=13,
        quality=2,
    )

    assert full_command == [
        "/smart/bin/ffmpeg",
        "-i",
        "/data/input.mp4",
        "-qscale:v",
        "2",
        "-start_number",
        "0",
        "-vf",
        "fps=5.0",
        "/data/frames/frame_%07d.jpg",
    ]
    assert range_command == [
        "/smart/bin/ffmpeg",
        "-i",
        "/data/input.mp4",
        "-vf",
        "select='between(n,10,12)'",
        "-vsync",
        "vfr",
        "-qscale:v",
        "2",
        "-copyts",
        "-start_number",
        "10",
        "/data/frames/frame_%07d.jpg",
    ]


@pytest.mark.unit
def test_build_filter_transcode_command_preserves_legacy_order():
    command = _build_filter_transcode_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        input_path=Path("/data/input.mp4"),
        output_path=Path("/data/output.mp4"),
        encoder_args=[
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
        ],
        extra_args=[
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-vf",
            "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
        ],
    )

    assert command == [
        "/smart/bin/ffmpeg",
        "-i",
        "/data/input.mp4",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        "/data/output.mp4",
    ]


@pytest.mark.unit
def test_extract_frame_range_numbers_outputs_by_requested_frame(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_dir = tmp_path / "frames"
    input_path.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(
        "endoreg_db.utils.video.frame_extraction._resolve_ffmpeg_executable",
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


@pytest.mark.unit
def test_extract_frames_numbers_full_extraction_from_zero(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_dir = tmp_path / "frames"
    input_path.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(
        "endoreg_db.utils.video.frame_extraction._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        output_dir.mkdir(parents=True, exist_ok=True)
        for frame_number in range(2):
            (output_dir / f"frame_{frame_number:07d}.jpg").write_bytes(b"frame")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ffmpeg_wrapper.extract_frames(
        input_path,
        output_dir,
        quality=2,
    )

    assert [path.name for path in result] == [
        "frame_0000000.jpg",
        "frame_0000001.jpg",
    ]
    assert "-start_number" in captured["command"]
    assert captured["command"][captured["command"].index("-start_number") + 1] == "0"


@pytest.mark.unit
def test_build_blacken_filter_expression_uses_frame_counter_ranges():
    expression = ffmpeg_wrapper._build_blacken_filter_expression(
        [(120, 240), (800, 900)]
    )

    assert "drawbox=" in expression
    assert "(gte(n\\,120)*lt(n\\,240))+(gte(n\\,800)*lt(n\\,900))" in expression


@pytest.mark.unit
def test_normalize_blacken_intervals_sorts_and_merges_ranges():
    intervals = [(40, 50), (10, 20), (15, 30), (30, 31), (80, 90)]

    normalized = ffmpeg_wrapper._normalize_blacken_intervals(intervals)

    assert normalized == [(10, 31), (40, 50), (80, 90)]


@pytest.mark.unit
def test_blacken_filter_args_switches_to_script_for_large_interval_sets(tmp_path):
    intervals = [(index * 10, index * 10 + 1) for index in range(121)]

    args, script_path = ffmpeg_wrapper._blacken_filter_args(
        intervals,
        inline_threshold=120,
        script_dir=tmp_path,
    )

    assert args[0] == "-filter_script:v"
    assert script_path is not None
    assert script_path.exists()
    assert script_path.parent == tmp_path
    assert "(gte(n\\,0)*lt(n\\,1))" in script_path.read_text(encoding="utf-8")
    script_path.unlink(missing_ok=True)


@pytest.mark.unit
def test_blacken_video_frame_intervals_maps_audio_and_filter(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(
        "endoreg_db.utils.video.masking_filters._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

    def fake_popen(command, **kwargs):
        captured["command"] = command
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ffmpeg_wrapper.blacken_video_frame_intervals(
        input_path,
        output_path,
        intervals=[(10, 20)],
        force_cpu=True,
    )

    assert result == output_path
    assert "-map" in captured["command"]
    assert captured["command"].count("-map") == 2
    assert "0:v:0" in captured["command"]
    assert "0:a?" in captured["command"]
    assert "-c:a" in captured["command"]
    assert captured["command"][captured["command"].index("-c:a") + 1] == "copy"
    assert "-vf" in captured["command"]
    assert (
        "(gte(n\\,10)*lt(n\\,20))"
        in captured["command"][captured["command"].index("-vf") + 1]
    )


@pytest.mark.unit
def test_blacken_video_frame_intervals_uses_video_filter_script_for_large_interval_sets(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    intervals = [(index * 10, index * 10 + 1) for index in range(121)]
    captured = {}

    monkeypatch.setattr(
        "endoreg_db.utils.video.masking_filters._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

    def fake_popen(command, **kwargs):
        captured["command"] = command
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ffmpeg_wrapper.blacken_video_frame_intervals(
        input_path,
        output_path,
        intervals=intervals,
        force_cpu=True,
    )

    assert result == output_path
    assert "-filter_script:v" in captured["command"]
    assert "-filter_complex_script" not in captured["command"]
    script_path = Path(
        captured["command"][captured["command"].index("-filter_script:v") + 1]
    )
    assert script_path.parent == tmp_path
    assert not script_path.exists()


@pytest.mark.unit
def test_build_roi_mask_and_blacken_filter_expression_combines_roi_and_intervals():
    expression = ffmpeg_wrapper._build_roi_mask_and_blacken_filter_expression(
        endo_roi={"x": 10, "y": 20, "width": 300, "height": 200},
        intervals=[(120, 240)],
    )

    assert "drawbox=x=0:y=0:w=iw:h=20:color=black:t=fill" in expression
    assert "drawbox=x=0:y=20:w=10:h=200:color=black:t=fill" in expression
    assert "drawbox=x=310:y=20:w=max(0\\,iw-310):h=200" in expression
    assert "drawbox=x=0:y=220:w=iw:h=max(0\\,ih-220)" in expression
    assert "(gte(n\\,120)*lt(n\\,240))" in expression


@pytest.mark.unit
def test_mask_video_to_roi_and_blacken_intervals_maps_audio_and_filter(
    monkeypatch,
    tmp_path,
):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(
        "endoreg_db.utils.video.masking_filters._resolve_ffmpeg_executable",
        lambda: "/smart/bin/ffmpeg",
    )

    def fake_popen(command, **kwargs):
        captured["command"] = command
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ffmpeg_wrapper.mask_video_to_roi_and_blacken_intervals(
        input_path,
        output_path,
        endo_roi={"x": 10, "y": 20, "width": 300, "height": 200},
        intervals=[(10, 20)],
        force_cpu=True,
    )

    assert result == output_path
    assert captured["command"].count("-map") == 2
    assert "0:v:0" in captured["command"]
    assert "0:a?" in captured["command"]
    assert "-c:a" in captured["command"]
    assert captured["command"][captured["command"].index("-c:a") + 1] == "copy"
    assert "-vf" in captured["command"]
    filter_expression = captured["command"][captured["command"].index("-vf") + 1]
    assert "drawbox=x=0:y=0:w=iw:h=20:color=black:t=fill" in filter_expression
    assert "(gte(n\\,10)*lt(n\\,20))" in filter_expression
