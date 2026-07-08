# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import logging
from enum import Enum
from pathlib import Path
from typing import List

logger = logging.getLogger("ffmpeg_wrapper")
_COMMON_FFMPEG_ARGS = ("-nostdin", "-hide_banner")
# stdin is disabled at subprocess boundary; ffprobe 8.1 rejects the ffmpeg-only
# -nostdin option.
_COMMON_FFPROBE_ARGS = ("-hide_banner",)
_LOSSLESS_FRAME_EXTENSIONS = frozenset({"png"})


class TimestampRepairMode(str, Enum):
    NONE = "none"
    GENERATE_PTS = "generate_pts"
    IGNORE_DTS = "ignore_dts"
    RESET_TO_ZERO = "reset_to_zero"


_TIMESTAMP_REPAIR_SEQUENCE = (
    TimestampRepairMode.GENERATE_PTS,
    TimestampRepairMode.IGNORE_DTS,
    TimestampRepairMode.RESET_TO_ZERO,
)


def _timestamp_repair_input_args(mode: TimestampRepairMode) -> List[str]:
    mapping: dict[TimestampRepairMode, list[str]] = {
        TimestampRepairMode.NONE: [],
        TimestampRepairMode.GENERATE_PTS: ["-fflags", "+genpts"],
        TimestampRepairMode.IGNORE_DTS: [
            "-fflags",
            "+genpts+igndts",
            "-err_detect",
            "ignore_err",
        ],
        TimestampRepairMode.RESET_TO_ZERO: [
            "-fflags",
            "+genpts+igndts",
            "-err_detect",
            "ignore_err",
        ],
    }
    return mapping[mode]


def _timestamp_repair_output_args(mode: TimestampRepairMode) -> List[str]:
    if mode == TimestampRepairMode.RESET_TO_ZERO:
        return ["-avoid_negative_ts", "make_zero", "-muxdelay", "0"]
    return []


def _build_transcode_command(
    *,
    ffmpeg_executable: str,
    input_path: Path,
    output_path: Path,
    encoder_args: List[str],
    audio_codec: str,
    audio_bitrate: str,
    extra_args: List[str] | None,
    timestamp_repair_mode: TimestampRepairMode,
) -> List[str]:
    disable_audio = _has_flag(extra_args, "-an")
    command = [
        ffmpeg_executable,
        *_COMMON_FFMPEG_ARGS,
        *_timestamp_repair_input_args(timestamp_repair_mode),
        "-i",
        str(input_path),
        *encoder_args,
    ]

    if not disable_audio:
        command.extend(["-c:a", audio_codec])
        if audio_codec != "copy":
            command.extend(["-b:a", audio_bitrate])

    command.extend(
        [
            *_timestamp_repair_output_args(timestamp_repair_mode),
            "-y",
        ]
    )

    if extra_args:
        command.extend(extra_args)
    command.append(str(output_path))
    return command


def _build_filter_transcode_command(
    *,
    ffmpeg_executable: str,
    input_path: Path,
    output_path: Path,
    encoder_args: List[str],
    extra_args: List[str],
) -> List[str]:
    return [
        ffmpeg_executable,
        *_COMMON_FFMPEG_ARGS,
        "-i",
        str(input_path),
        *encoder_args,
        *extra_args,
        "-y",
        str(output_path),
    ]


def _build_ffprobe_stream_info_command(
    *,
    ffprobe_executable: str,
    file_path: Path,
) -> List[str]:
    return [
        ffprobe_executable,
        *_COMMON_FFPROBE_ARGS,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        str(file_path),
    ]


def _build_extract_frames_command(
    *,
    ffmpeg_executable: str,
    video_path: Path,
    output_pattern: Path,
    quality: int,
    fps: float | None,
    ext: str,
) -> List[str]:
    cmd = [
        ffmpeg_executable,
        *_COMMON_FFMPEG_ARGS,
        "-i",
        str(video_path),
        "-start_number",
        "0",
    ]

    if fps is not None:
        cmd.extend(["-vf", f"fps={fps}"])

    cmd.extend(_frame_image_encoder_args(ext=ext, quality=quality))
    cmd.append(str(output_pattern))
    return cmd


def _build_extract_frame_range_command(
    *,
    ffmpeg_executable: str,
    video_path: Path,
    output_pattern: Path,
    start_frame: int,
    end_frame: int,
    quality: int,
    ext: str,
) -> List[str]:
    select_filter = f"select='between(n,{start_frame},{end_frame - 1})'"
    return [
        ffmpeg_executable,
        *_COMMON_FFMPEG_ARGS,
        "-i",
        str(video_path),
        "-vf",
        select_filter,
        "-vsync",
        "vfr",
        *_frame_image_encoder_args(ext=ext, quality=quality),
        "-copyts",
        "-start_number",
        str(start_frame),
        str(output_pattern),
    ]


def _update_or_append_ffmpeg_arg(args: List[str], key: str, value: str) -> None:
    """Set an FFmpeg key/value option without assuming the value already exists."""
    try:
        index = args.index(key)
    except ValueError:
        args.extend([key, value])
        return

    value_index = index + 1
    if value_index >= len(args):
        logger.error("Missing value for %s argument. Appending required value.", key)
        args.append(value)
        return

    if args[value_index] != value:
        logger.warning(
            "Overriding existing %s '%s' with '%s'",
            key,
            args[value_index],
            value,
        )
        args[value_index] = value


def _has_flag(args: List[str] | None, flag: str) -> bool:
    return args is not None and flag in args


def _frame_image_encoder_args(*, ext: str, quality: int) -> list[str]:
    normalized_ext = ext.lower().lstrip(".")
    if normalized_ext in _LOSSLESS_FRAME_EXTENSIONS:
        return ["-compression_level", "0"]
    return ["-qscale:v", str(quality)]
