from collections.abc import Callable
from endoreg_db.services.video_storage import frame_provenance

# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import subprocess
import signal
from pathlib import Path
from typing import NoReturn

import pytest
from endoreg_db.utils.ffmpeg_wrapper import _build_encoder_args, transcode_video
from lx_dtypes.models.contracts.json_types import JsonObject

from endoreg_db.utils import transcode_execution
from endoreg_db.utils import ffmpeg_wrapper
from endoreg_db.services.video_storage.probes import probe_video_frame_timestamps
from endoreg_db.services.video_storage.contracts import configured_video_storage_profile
from endoreg_db.utils.video.command_construction import (
    FFprobeInputPolicy,
    TimestampRepairMode,
    _build_extract_frame_range_command,
    _build_extract_frames_command,
    _build_ffprobe_stream_info_command,
    _build_filter_transcode_command,
    _build_transcode_command,
)


class FakePopen:
    command: list[str]
    returncode: int
    stderr: str
    timeout: bool
    killed: bool
    kwargs: dict[str, str | int | bool]

    def __init__(
        self,
        command: list[str],
        *,
        returncode: int = 0,
        stderr_output: str = "",
        timeout: bool = False,
        **kwargs: str | int | bool,
    ) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr_output
        self.timeout = timeout
        self.killed = False
        self.kwargs = kwargs

    def communicate(self, timeout: float = 0.0) -> tuple[str, str]:
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired(cmd=self.command, timeout=timeout)
        return "", self.stderr

    def kill(self) -> None:
        self.killed = True


def _smart_ffmpeg_path() -> str:
    return "/smart/bin/ffmpeg"


def _valid_h264_stream_info(_path: Path) -> JsonObject:
    return {"streams": [{"codec_type": "video", "codec_name": "h264"}]}


def _empty_stream_info(_path: Path) -> JsonObject:
    return {"streams": []}


def _preferred_nvenc_encoder() -> dict[str, str]:
    return {
        "name": "h264_nvenc",
        "preset_param": "-preset",
        "preset_value": "p4",
        "quality_param": "-cq",
        "quality_value": "20",
        "type": "nvenc",
        "fallback_preset": "p1",
    }


def _yuvj420p_full_range_stream_info(_path: Path) -> JsonObject:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuvj420p",
                "color_range": "pc",
            }
        ]
    }


def _yuvj420p_limited_range_stream_info(_path: Path) -> JsonObject:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuvj420p",
                "color_range": "tv",
            }
        ]
    }


@pytest.fixture(autouse=True)
def _valid_transcode_output_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    # These subprocess-policy tests use fake bytes; real provenance capture is
    # covered by test_frame_provenance against independently decoded images.
    def run_fake_encoder(
        command: list[str], runner: Callable[[list[str]], tuple[int, str]]
    ) -> tuple[int, str]:
        return runner(command)

    monkeypatch.setattr(frame_provenance, "run_with_frame_provenance", run_fake_encoder)
    monkeypatch.setattr(
        transcode_execution,
        "get_stream_info",
        _valid_h264_stream_info,
    )


@pytest.mark.unit
def test_transcode_video_timeout_removes_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    output_path.write_bytes(b"partial")
    monkeypatch.setattr(
        "endoreg_db.utils.transcode_execution._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    created_processes: list[FakePopen] = []

    def fake_popen(command: list[str], **_kwargs: str | int | bool) -> FakePopen:
        process = FakePopen(command, timeout=True)
        created_processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = transcode_video(input_path, output_path, force_cpu=True)

    assert result is None
    assert not output_path.exists()
    assert created_processes
    assert created_processes[0].killed


@pytest.mark.unit
def test_ffmpeg_timeout_terminates_the_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakePopen(["ffmpeg"], timeout=True)
    setattr(process, "pid", 4242)
    signals: list[tuple[int, signal.Signals]] = []

    def fake_killpg(pid: int, sent_signal: signal.Signals) -> None:
        signals.append((pid, sent_signal))
        process.killed = True

    def fake_popen(
        command: list[str],
        **_kwargs: str | int | bool,
    ) -> FakePopen:
        assert command == ["ffmpeg"]
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(transcode_execution.os, "name", "posix")
    monkeypatch.setattr(transcode_execution.os, "killpg", fake_killpg)

    with pytest.raises(subprocess.TimeoutExpired):
        transcode_execution._run_ffmpeg_command(["ffmpeg"])

    assert signals == [(4242, signal.SIGTERM)]


@pytest.mark.unit
def test_transcode_video_force_cpu_uses_cpu_only_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    monkeypatch.setattr(
        "endoreg_db.utils.transcode_execution._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    captured_commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: str | int | bool) -> FakePopen:
        captured_commands.append(command)
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0)

    monkeypatch.setattr(
        "endoreg_db.utils.video.encoder_policy._get_preferred_encoder",
        _preferred_nvenc_encoder,
    )
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = transcode_video(input_path, output_path, force_cpu=True)

    assert result == output_path
    command = captured_commands[0]
    assert command[0] == "/smart/bin/ffmpeg"
    assert "-c:v" in command
    codec_index = command.index("-c:v")
    assert command[codec_index + 1] == "libx264"
    assert "-crf" in command
    assert "-cq" not in command
    assert "-gpu" not in command
    assert "-rc" not in command
    assert command == [
        "/smart/bin/ffmpeg",
        "-nostdin",
        "-hide_banner",
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
        "-y",
        "-profile:v",
        "high",
        "-vf",
        "scale=iw:ih:in_range=auto:out_range=full,format=yuv420p",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "pc",
        "-copyts",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "demux",
        "-an",
        str(output_path),
    ]


@pytest.mark.unit
def test_transcode_output_validation_rejects_non_video_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "output.mp4"
    output_path.write_bytes(b"not empty but not video")
    monkeypatch.setattr(
        transcode_execution,
        "get_stream_info",
        _empty_stream_info,
    )

    assert ffmpeg_wrapper._transcode_output_is_valid(output_path) is False


@pytest.mark.unit
@pytest.mark.parametrize("preserve_timestamps", [False, True])
def test_transcode_video_retries_timestamp_repair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    preserve_timestamps: bool,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    monkeypatch.setattr(
        "endoreg_db.utils.transcode_execution._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: str | int | bool) -> FakePopen:
        commands.append(command)
        if len(commands) == 1:
            return FakePopen(
                command,
                returncode=1,
                stderr_output="invalid dts: timestamp too large and out of range",
            )
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = transcode_video(
        input_path,
        output_path,
        force_cpu=True,
        extra_args=None if preserve_timestamps else ["-fpsmax", "50"],
    )

    if preserve_timestamps:
        assert result is None
        assert len(commands) == 1
        assert not output_path.exists()
        return

    assert result == output_path
    assert len(commands) == 2
    assert "-fflags" not in commands[0]
    assert "-fflags" in commands[1]
    assert commands[1][commands[1].index("-fflags") + 1] == "+genpts"


@pytest.mark.unit
def test_transcode_videofile_if_required_accepts_full_range_yuvj420p_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")

    monkeypatch.setattr(
        transcode_execution,
        "get_stream_info",
        _yuvj420p_full_range_stream_info,
    )

    def fail_if_transcoded(
        _input_path: Path,
        _output_path: Path,
        **_kwargs: str | int | bool,
    ) -> NoReturn:
        raise AssertionError("full-range yuvj420p should not be transcoded")

    monkeypatch.setattr(transcode_execution, "transcode_video", fail_if_transcoded)

    result = transcode_execution.transcode_videofile_if_required(
        input_path,
        output_path,
    )

    assert result == output_path
    assert output_path.read_bytes() == b"input"


@pytest.mark.unit
def test_transcode_videofile_if_required_rejects_yuvj420p_without_full_range(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    called = False

    monkeypatch.setattr(
        transcode_execution,
        "get_stream_info",
        _yuvj420p_limited_range_stream_info,
    )

    def fake_transcode(
        _input_path: Path,
        output_path: Path,
        **_kwargs: str | int | bool,
    ) -> Path:
        nonlocal called
        called = True
        output_path.write_bytes(b"transcoded")
        return output_path

    monkeypatch.setattr(transcode_execution, "transcode_video", fake_transcode)

    result = transcode_execution.transcode_videofile_if_required(
        input_path,
        output_path,
    )

    assert called is True
    assert result == output_path
    assert output_path.read_bytes() == b"transcoded"


@pytest.mark.unit
def test_build_encoder_args_nvenc_forces_yuv420p_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "endoreg_db.utils.video.encoder_policy._get_preferred_encoder",
        _preferred_nvenc_encoder,
    )

    encoder_args, encoder_type = _build_encoder_args()

    assert encoder_type == "nvenc"
    assert "-vf" in encoder_args
    assert encoder_args[encoder_args.index("-vf") + 1] == "format=yuv420p"


@pytest.mark.unit
def test_ffmpeg_timestamp_fault_detection_requires_timestamp_and_fault_signal() -> None:
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
def test_update_or_append_ffmpeg_arg_replaces_appends_and_repairs_missing_value() -> (
    None
):
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
def test_build_transcode_command_preserves_legacy_extra_arg_order() -> None:
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
        "-nostdin",
        "-hide_banner",
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
        "-y",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "pc",
        "-an",
        "/data/output.mp4",
    ]


@pytest.mark.unit
def test_build_transcode_command_preserves_legacy_timestamp_repair_order() -> None:
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
        "-nostdin",
        "-hide_banner",
        "-fflags",
        "+genpts+igndts",
        "-err_detect",
        "ignore_err",
        "-i",
        "/data/input.mp4",
        "-c:v",
        "libx264",
        "-avoid_negative_ts",
        "make_zero",
        "-muxdelay",
        "0",
        "-y",
        "-an",
        "/data/output.mp4",
    ]


@pytest.mark.unit
def test_build_ffprobe_stream_info_command_omits_ffmpeg_only_nostdin() -> None:
    command = _build_ffprobe_stream_info_command(
        ffprobe_executable="/smart/bin/ffprobe",
        file_path=Path("/data/input.mp4"),
    )

    assert command == [
        "/smart/bin/ffprobe",
        "-hide_banner",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        "/data/input.mp4",
    ]
    assert "-nostdin" not in command


@pytest.mark.unit
def test_build_ffprobe_stream_info_command_allows_trusted_local_hls_key() -> None:
    command = _build_ffprobe_stream_info_command(
        ffprobe_executable="/smart/bin/ffprobe",
        file_path=Path("/protected/.profile-validation.m3u8"),
        input_policy=FFprobeInputPolicy.TRUSTED_LOCAL_HLS,
    )

    assert command == [
        "/smart/bin/ffprobe",
        "-hide_banner",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        "-allowed_extensions",
        "ALL",
        "/protected/.profile-validation.m3u8",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("audio_codec", ["copy", "aac"])
def test_build_transcode_command_always_disables_audio(
    audio_codec: str,
) -> None:
    command = _build_transcode_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        input_path=Path("/data/input.mp4"),
        output_path=Path("/data/output.mp4"),
        encoder_args=["-c:v", "libx264"],
        audio_codec=audio_codec,
        audio_bitrate="128k",
        extra_args=None,
        timestamp_repair_mode=TimestampRepairMode.NONE,
    )

    assert "-an" in command
    assert "-c:a" not in command
    assert "-b:a" not in command


@pytest.mark.unit
def test_build_frame_extraction_commands_preserve_legacy_order() -> None:
    output_pattern = Path("/data/frames/frame_%07d.jpg")

    full_command = _build_extract_frames_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        video_path=Path("/data/input.mp4"),
        output_pattern=output_pattern,
        quality=2,
        fps=5.0,
        ext="jpg",
    )
    range_command = _build_extract_frame_range_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        video_path=Path("/data/input.mp4"),
        output_pattern=output_pattern,
        start_frame=10,
        end_frame=13,
        quality=2,
        ext="jpg",
    )

    assert full_command == [
        "/smart/bin/ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        "/data/input.mp4",
        "-start_number",
        "0",
        "-an",
        "-vf",
        "fps=5.0",
        "-qscale:v",
        "2",
        "/data/frames/frame_%07d.jpg",
    ]
    assert range_command == [
        "/smart/bin/ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-i",
        "/data/input.mp4",
        "-vf",
        "select='between(n,10,12)'",
        "-fps_mode",
        "passthrough",
        "-qscale:v",
        "2",
        "-copyts",
        "-start_number",
        "10",
        "-an",
        "/data/frames/frame_%07d.jpg",
    ]


@pytest.mark.unit
def test_build_png_frame_extraction_commands_disable_png_compression() -> None:
    output_pattern = Path("/data/frames/frame_%07d.png")

    full_command = _build_extract_frames_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        video_path=Path("/data/input.mp4"),
        output_pattern=output_pattern,
        quality=2,
        fps=None,
        ext="png",
    )
    range_command = _build_extract_frame_range_command(
        ffmpeg_executable="/smart/bin/ffmpeg",
        video_path=Path("/data/input.mp4"),
        output_pattern=output_pattern,
        start_frame=10,
        end_frame=13,
        quality=2,
        ext="png",
    )

    assert "-qscale:v" not in full_command
    assert full_command[full_command.index("-fps_mode") + 1] == "passthrough"
    assert "-compression_level" in full_command
    assert full_command[full_command.index("-compression_level") + 1] == "0"
    assert "-qscale:v" not in range_command
    assert "-compression_level" in range_command
    assert range_command[range_command.index("-compression_level") + 1] == "0"


@pytest.mark.unit
def test_build_filter_transcode_command_preserves_legacy_order() -> None:
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
        "-nostdin",
        "-hide_banner",
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
        "-an",
        "/data/output.mp4",
    ]


@pytest.mark.unit
def test_extract_frame_range_numbers_outputs_by_requested_frame(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_dir = tmp_path / "frames"
    input_path.write_bytes(b"video")
    captured_commands: list[list[str]] = []
    captured_kwargs: list[dict[str, object]] = []

    monkeypatch.setattr(
        "endoreg_db.utils.video.frame_extraction._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_commands.append(command)
        captured_kwargs.append(_kwargs)
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
    command = captured_commands[0]
    assert "-start_number" in command
    assert command[command.index("-start_number") + 1] == "10"
    assert captured_kwargs[0]["stdin"] == subprocess.DEVNULL


def _patch_presentation_timestamp_tools(
    monkeypatch: pytest.MonkeyPatch,
    output_dir: Path,
    *,
    container_start: str,
) -> list[list[str]]:
    captured_commands: list[list[str]] = []
    monkeypatch.setattr(
        "endoreg_db.utils.video.frame_extraction._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )
    monkeypatch.setattr(
        "endoreg_db.utils.video.frame_extraction._resolve_ffprobe_executable",
        lambda: "/usr/bin/ffprobe",
    )

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if command[0].endswith("ffprobe"):
            return subprocess.CompletedProcess(command, 0, f"{container_start}\n", "")
        captured_commands.append(command)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "frame_0000000.jpg").write_bytes(b"first")
        (output_dir / "frame_0000001.jpg").write_bytes(b"second")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured_commands


@pytest.mark.unit
@pytest.mark.parametrize(
    ("container_start", "expected_seeks"),
    [
        ("0.000000", ("0.500000000", "1.602488889")),
        ("0.250000", ("0.250000000", "1.352488889")),
        # The seek is clamped at zero when the first frame is the container start.
        ("0.500000", ("0.000000000", "1.102488889")),
    ],
)
def test_extract_frames_by_presentation_timestamp_seeks_relative_to_container_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    container_start: str,
    expected_seeks: tuple[str, str],
) -> None:
    input_path = tmp_path / "input.mp4"
    output_dir = tmp_path / "frames"
    input_path.write_bytes(b"video")
    captured_commands = _patch_presentation_timestamp_tools(
        monkeypatch, output_dir, container_start=container_start
    )

    result = ffmpeg_wrapper.extract_frames_by_presentation_timestamp(
        input_path,
        output_dir,
        [144_224, 45_000],
        time_base_num=1,
        time_base_den=90_000,
        quality=2,
    )

    assert len(result) == 2
    assert len(captured_commands) == 2
    first_command = captured_commands[0]
    second_command = captured_commands[1]
    assert first_command[first_command.index("-ss") + 1] == expected_seeks[0]
    assert second_command[second_command.index("-ss") + 1] == expected_seeks[1]
    assert first_command.index("-noaccurate_seek") < first_command.index("-ss")
    assert first_command[first_command.index("-map") + 1] == "0:v:0"
    assert first_command[first_command.index("-vf") + 1] == ("select='eq(pts\\,45000)'")
    assert second_command[second_command.index("-vf") + 1] == (
        "select='eq(pts\\,144224)'"
    )
    assert first_command[first_command.index("-fps_mode") + 1] == "passthrough"


@pytest.mark.unit
@pytest.mark.parametrize("container_start", ["N/A", ""])
def test_extract_frames_by_presentation_timestamp_rejects_unknown_container_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    container_start: str,
) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video")
    captured_commands = _patch_presentation_timestamp_tools(
        monkeypatch, tmp_path / "frames", container_start=container_start
    )

    with pytest.raises(RuntimeError, match="start time"):
        ffmpeg_wrapper.extract_frames_by_presentation_timestamp(
            input_path,
            tmp_path / "frames",
            [45_000],
            time_base_num=1,
            time_base_den=90_000,
            quality=2,
        )
    assert captured_commands == []


def _rgb_frame_digests(executable: str, source: Path) -> list[str]:
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "framemd5",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.rsplit(",", 1)[-1].strip()
        for line in completed.stdout.splitlines()
        if line and not line.startswith("#")
    ]


@pytest.mark.ffmpeg
@pytest.mark.integration
@pytest.mark.parametrize("container_offset_seconds", ["0", "7.3"])
def test_real_presentation_timestamp_extraction_matches_decoded_frames(
    tmp_path: Path,
    container_offset_seconds: str,
) -> None:
    ffmpeg_executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
    ffprobe_executable = ffmpeg_wrapper.resolve_ffprobe_executable()
    if ffmpeg_executable is None or ffprobe_executable is None:
        pytest.skip("ffmpeg and ffprobe are not available")

    # Concatenating 30 and 24 frames-per-second parts yields variable frame
    # spacing; the copy with a container offset reproduces nonzero origins.
    parts: list[Path] = []
    for rate in (30, 24):
        part = tmp_path / f"part_{rate}.mp4"
        subprocess.run(
            [
                ffmpeg_executable,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=96x64:rate={rate}",
                "-frames:v",
                "12",
                "-c:v",
                "libx264",
                "-g",
                "5",
                "-video_track_timescale",
                "90000",
                "-y",
                str(part),
            ],
            check=True,
            capture_output=True,
        )
        parts.append(part)
    concat_list = tmp_path / "parts.txt"
    concat_list.write_text("".join(f"file '{part}'\n" for part in parts))
    source = tmp_path / "variable_frame_rate.mp4"
    subprocess.run(
        [
            ffmpeg_executable,
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-output_ts_offset",
            container_offset_seconds,
            "-y",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    probed = subprocess.run(
        [
            ffprobe_executable,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp",
            "-of",
            "csv=p=0",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    timestamps = [int(value.strip(",")) for value in probed.stdout.split()]
    assert len(set(timestamps)) == 24
    reference_digests = _rgb_frame_digests(ffmpeg_executable, source)

    # Sparse selection across both frame-rate parts and both ends of the clip.
    selected_indices = [0, 5, 11, 12, 17, 23]
    extracted = ffmpeg_wrapper.extract_frames_by_presentation_timestamp(
        source,
        tmp_path / "frames",
        [timestamps[index] for index in selected_indices],
        time_base_num=1,
        time_base_den=90_000,
        quality=2,
        ext="png",
    )

    assert [_rgb_frame_digests(ffmpeg_executable, path)[0] for path in extracted] == [
        reference_digests[index] for index in selected_indices
    ]


@pytest.mark.unit
def test_extract_frames_numbers_full_extraction_from_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_dir = tmp_path / "frames"
    input_path.write_bytes(b"video")
    captured_commands: list[list[str]] = []
    captured_kwargs: list[dict[str, object]] = []

    monkeypatch.setattr(
        "endoreg_db.utils.video.frame_extraction._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_commands.append(command)
        captured_kwargs.append(_kwargs)
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
    command = captured_commands[0]
    assert "-start_number" in command
    assert command[command.index("-start_number") + 1] == "0"
    assert captured_kwargs[0]["stdin"] == subprocess.DEVNULL


@pytest.mark.unit
def test_build_blacken_filter_expression_uses_frame_counter_ranges() -> None:
    expression = ffmpeg_wrapper._build_blacken_filter_expression(
        [(120, 240), (800, 900)]
    )

    assert "drawbox=" in expression
    assert "(gte(n\\,120)*lt(n\\,240))+(gte(n\\,800)*lt(n\\,900))" in expression


@pytest.mark.unit
def test_normalize_blacken_intervals_sorts_and_merges_ranges() -> None:
    intervals = [(40, 50), (10, 20), (15, 30), (30, 31), (80, 90)]

    normalized = ffmpeg_wrapper._normalize_blacken_intervals(intervals)

    assert normalized == [(10, 31), (40, 50), (80, 90)]


@pytest.mark.unit
def test_blacken_filter_args_switches_to_script_for_large_interval_sets(
    tmp_path: Path,
) -> None:
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
@pytest.mark.parametrize("bitrate_cap", [None, 8_000_000])
def test_blacken_video_frame_intervals_disables_audio_and_applies_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bitrate_cap: int | None,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    captured_commands: list[list[str]] = []

    monkeypatch.setattr(
        "endoreg_db.utils.video.masking_filters._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    def fake_popen(command: list[str], **_kwargs: str | int | bool) -> FakePopen:
        captured_commands.append(command)
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ffmpeg_wrapper.blacken_video_frame_intervals(
        input_path,
        output_path,
        intervals=[(10, 20)],
        force_cpu=True,
        max_bit_rate_bps=bitrate_cap,
    )

    assert result == output_path
    command = captured_commands[0]
    assert "-map" in command
    assert command.count("-map") == 1
    assert "0:v:0" in command
    assert "0:a?" not in command
    assert "-c:a" not in command
    assert "-an" in command
    assert "-vf" in command
    assert "(gte(n\\,10)*lt(n\\,20))" in command[command.index("-vf") + 1]
    assert "out_range=full" in command[command.index("-vf") + 1]
    assert command[command.index("-color_range") + 1] == "pc"
    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert "-fpsmax" not in command
    assert "-r" not in command
    if bitrate_cap is not None:
        assert command[command.index("-b:v") + 1] == str(bitrate_cap)
        assert command[command.index("-maxrate") + 1] == str(bitrate_cap)
        assert command[command.index("-bufsize") + 1] == str(bitrate_cap * 2)


@pytest.mark.parametrize(
    "operation,bitrate_cap",
    [
        ("blacken", None),
        ("blacken", 800_000),
        ("default", None),
        ("storage", None),
        ("roi", None),
    ],
)
@pytest.mark.parametrize("origin", [0.0, 1.4])
def test_real_transcoding_preserves_irregular_native_timestamps(
    tmp_path: Path,
    operation: str,
    bitrate_cap: int | None,
    origin: float,
) -> None:
    executable = ffmpeg_wrapper.resolve_ffmpeg_executable()
    assert executable is not None, (
        "FFmpeg is required for native timestamp verification"
    )
    source = tmp_path / "native.mp4"
    output = tmp_path / "blackened.mp4"
    subprocess.run(
        [
            executable,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=60:duration=1",
            "-vf",
            f"settb=1/60000,select='not(eq(mod(n,5),2))',"
            f"setpts='PTS+if(eq(mod(N,3),1),180,0)+{origin}/TB'",
            "-fps_mode",
            "passthrough",
            "-enc_time_base",
            "filter",
            "-c:v",
            "libx264",
            "-an",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    before = probe_video_frame_timestamps(source)
    assert len(before) == 48
    if operation == "blacken":
        result = ffmpeg_wrapper.blacken_video_frame_intervals(
            source,
            output,
            intervals=[(10, 20)],
            force_cpu=True,
            max_bit_rate_bps=bitrate_cap,
        )
    elif operation == "roi":
        result = ffmpeg_wrapper.mask_video_to_roi_and_blacken_intervals(
            source,
            output,
            endo_roi={"x": 4, "y": 4, "width": 56, "height": 56},
            intervals=[(10, 20)],
            force_cpu=True,
        )
    else:
        result = transcode_video(
            source,
            output,
            force_cpu=True,
            extra_args=configured_video_storage_profile().ffmpeg_output_args()
            if operation == "storage"
            else None,
        )
    assert result == output
    after = probe_video_frame_timestamps(output)
    assert len(after) == len(before)
    assert all(
        abs(new.presentation_time_seconds - old.presentation_time_seconds) <= 1e-6
        for old, new in zip(before, after, strict=True)
    )


@pytest.mark.parametrize("bitrate_cap", [0, -1, 2**63])
def test_blackening_rejects_invalid_bitrate_caps(
    tmp_path: Path, bitrate_cap: int
) -> None:
    with pytest.raises(ValueError, match="bitrate cap"):
        ffmpeg_wrapper.blacken_video_frame_intervals(
            tmp_path / "input.mp4",
            tmp_path / "output.mp4",
            intervals=[(0, 1)],
            max_bit_rate_bps=bitrate_cap,
        )


@pytest.mark.unit
def test_blacken_video_frame_intervals_uses_video_filter_script_for_large_interval_sets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    intervals = [(index * 10, index * 10 + 1) for index in range(121)]
    captured_commands: list[list[str]] = []

    monkeypatch.setattr(
        "endoreg_db.utils.video.masking_filters._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    def fake_popen(command: list[str], **_kwargs: str | int | bool) -> FakePopen:
        captured_commands.append(command)
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ffmpeg_wrapper.blacken_video_frame_intervals(
        input_path,
        output_path,
        intervals=intervals,
        force_cpu=True,
    )

    assert result == output_path
    command = captured_commands[0]
    assert "-filter_script:v" in command
    assert "-filter_complex_script" not in command
    script_path = Path(command[command.index("-filter_script:v") + 1])
    assert script_path.parent == tmp_path
    assert not script_path.exists()


@pytest.mark.unit
def test_build_roi_mask_and_blacken_filter_expression_combines_roi_and_intervals() -> (
    None
):
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
def test_mask_video_to_roi_and_blacken_intervals_disables_audio_and_applies_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"video")
    captured_commands: list[list[str]] = []

    monkeypatch.setattr(
        "endoreg_db.utils.video.masking_filters._resolve_ffmpeg_executable",
        _smart_ffmpeg_path,
    )

    def fake_popen(command: list[str], **_kwargs: str | int | bool) -> FakePopen:
        captured_commands.append(command)
        output_path.write_bytes(b"encoded")
        return FakePopen(command, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = ffmpeg_wrapper.mask_video_to_roi_and_blacken_intervals(
        input_path,
        output_path,
        endo_roi={"x": 10, "y": 20, "width": 300, "height": 200},
        intervals=[(10, 20)],
        force_cpu=True,
    )

    assert result == output_path
    command = captured_commands[0]
    assert command.count("-map") == 1
    assert "0:v:0" in command
    assert "0:a?" not in command
    assert "-c:a" not in command
    assert "-an" in command
    assert "-vf" in command
    filter_expression = command[command.index("-vf") + 1]
    assert "drawbox=x=0:y=0:w=iw:h=20:color=black:t=fill" in filter_expression
    assert "(gte(n\\,10)*lt(n\\,20))" in filter_expression
    assert "out_range=full" in filter_expression
    assert command[command.index("-color_range") + 1] == "pc"
    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert "-fpsmax" not in command
    assert "-r" not in command
