# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Iterable, Optional

from lx_dtypes.models.contracts.endoscopy_processor import (
    RoiBoxCore,
    roi_box_from_object,
)

from endoreg_db.utils.file_operations import (
    atomic_write_file,
    ensure_directory,
    safe_unlink_file,
)

from .command_construction import (
    _build_filter_transcode_command,
    _update_or_append_ffmpeg_arg,
)
from .encoder_policy import _build_encoder_args
from .encoding_standard import STANDARD_VIDEO_ENCODING
from .executable_discovery import _resolve_ffmpeg_executable
from ..transcode_execution import (
    FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
    _delete_partial_output,
    _run_ffmpeg_command,
    _transcode_output_is_valid,
)

logger = logging.getLogger("ffmpeg_wrapper")


def _normalize_blacken_intervals(
    intervals: Iterable[tuple[int, int]],
) -> list[tuple[int, int]]:
    normalized_intervals: list[tuple[int, int]] = []
    for start_frame, end_frame in intervals:
        start = int(start_frame)
        end = int(end_frame)
        if start < 0 or end <= start:
            raise ValueError(
                "Blackening intervals must be half-open ranges with "
                f"0 <= start < end: start={start} end={end}"
            )
        normalized_intervals.append((start, end))
    if not normalized_intervals:
        raise ValueError("At least one interval is required to build a filter.")

    normalized_intervals.sort()
    merged_intervals: list[tuple[int, int]] = [normalized_intervals[0]]
    for start_frame, end_frame in normalized_intervals[1:]:
        previous_start, previous_end = merged_intervals[-1]
        if start_frame <= previous_end:
            merged_intervals[-1] = (previous_start, max(previous_end, end_frame))
        else:
            merged_intervals.append((start_frame, end_frame))
    return merged_intervals


def _build_blacken_filter_expression_from_normalized(
    normalized_intervals: list[tuple[int, int]],
) -> str:
    enable_expression = "+".join(
        f"(gte(n\\,{start_frame})*lt(n\\,{end_frame}))"
        for start_frame, end_frame in normalized_intervals
    )
    return f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{enable_expression}'"


def _build_blacken_filter_expression(
    intervals: Iterable[tuple[int, int]],
) -> str:
    normalized_intervals = _normalize_blacken_intervals(intervals)
    return _build_blacken_filter_expression_from_normalized(normalized_intervals)


def _normalize_video_roi(endo_roi: RoiBoxCore | object) -> tuple[int, int, int, int]:
    try:
        roi = roi_box_from_object(endo_roi)
        x = int(roi.x)
        y = int(roi.y)
        width = int(roi.width)
        height = int(roi.height)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Endoscope ROI must define integer x, y, width, and height."
        ) from exc

    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError(
            "Endoscope ROI must satisfy x >= 0, y >= 0, width > 0, and height > 0."
        )
    return x, y, width, height


def _build_roi_mask_filter_expressions(endo_roi: RoiBoxCore | object) -> list[str]:
    """Build drawbox filters that keep the ROI visible and blacken the rest."""

    x, y, width, height = _normalize_video_roi(endo_roi)
    right = x + width
    bottom = y + height
    filters: list[str] = []

    if y > 0:
        filters.append(f"drawbox=x=0:y=0:w=iw:h={y}:color=black:t=fill")
    if x > 0:
        filters.append(f"drawbox=x=0:y={y}:w={x}:h={height}:color=black:t=fill")
    filters.append(
        f"drawbox=x={right}:y={y}:w=max(0\\,iw-{right}):h={height}:color=black:t=fill"
    )
    filters.append(
        f"drawbox=x=0:y={bottom}:w=iw:h=max(0\\,ih-{bottom}):color=black:t=fill"
    )
    return filters


def _build_roi_mask_and_blacken_filter_expression(
    *,
    endo_roi: RoiBoxCore | object,
    intervals: Iterable[tuple[int, int]] = (),
) -> str:
    filter_parts = _build_roi_mask_filter_expressions(endo_roi)
    interval_list = list(intervals)
    if interval_list:
        filter_parts.append(_build_blacken_filter_expression(interval_list))
    filter_parts.append(STANDARD_VIDEO_ENCODING.filter_chain())
    return ",".join(filter_parts)


def _roi_mask_and_blacken_filter_args(
    *,
    endo_roi: RoiBoxCore | object,
    intervals: Iterable[tuple[int, int]] = (),
    inline_threshold: int = 120,
    script_dir: Path | None = None,
) -> tuple[list[str], Path | None]:
    interval_list = list(intervals)
    filter_expression = _build_roi_mask_and_blacken_filter_expression(
        endo_roi=endo_roi,
        intervals=interval_list,
    )
    if len(interval_list) <= inline_threshold:
        return ["-vf", filter_expression], None

    target_dir = ensure_directory(Path(script_dir or tempfile.gettempdir()))
    script_path = target_dir / f"roi-mask-blackening-{uuid.uuid4().hex}.ffmpeg-filter"
    script_content = f"{filter_expression}\n".encode("utf-8")
    atomic_write_file(
        destination=script_path,
        content=[script_content],
        required_bytes=len(script_content),
    )
    return ["-filter_script:v", str(script_path)], script_path


def _blacken_filter_args(
    intervals: Iterable[tuple[int, int]],
    *,
    inline_threshold: int = 120,
    script_dir: Path | None = None,
) -> tuple[list[str], Path | None]:
    normalized_intervals = _normalize_blacken_intervals(intervals)
    return _blacken_filter_args_from_normalized(
        normalized_intervals,
        inline_threshold=inline_threshold,
        script_dir=script_dir,
    )


def _blacken_filter_args_from_normalized(
    normalized_intervals: list[tuple[int, int]],
    *,
    inline_threshold: int = 120,
    script_dir: Path | None = None,
) -> tuple[list[str], Path | None]:
    filter_expression = _build_blacken_filter_expression_from_normalized(
        normalized_intervals
    )
    filter_expression = f"{filter_expression},{STANDARD_VIDEO_ENCODING.filter_chain()}"
    if len(normalized_intervals) <= inline_threshold:
        return ["-vf", filter_expression], None

    target_dir = ensure_directory(Path(script_dir or tempfile.gettempdir()))
    script_path = target_dir / f"outside-blackening-{uuid.uuid4().hex}.ffmpeg-filter"
    script_content = f"{filter_expression}\n".encode("utf-8")
    atomic_write_file(
        destination=script_path,
        content=[script_content],
        required_bytes=len(script_content),
    )
    return ["-filter_script:v", str(script_path)], script_path


def blacken_video_frame_intervals(
    input_path: Path,
    output_path: Path,
    *,
    intervals: Iterable[tuple[int, int]],
    quality_mode: str = "balanced",
    force_cpu: bool = False,
) -> Optional[Path]:
    if not input_path.exists():
        logger.error("Input file not found for outside blackening: %s", input_path)
        return None

    normalized_intervals = _normalize_blacken_intervals(intervals)

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        logger.error(
            "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        )
        return None

    ensure_directory(output_path.parent)

    encoder_args, _encoder_type = _build_encoder_args(
        quality_mode,
        fallback=False,
        custom_crf=None,
        encoder_type_override="cpu" if force_cpu else None,
    )
    if "-vf" in encoder_args:
        vf_index = encoder_args.index("-vf")
        del encoder_args[vf_index : vf_index + 2]

    filter_args, filter_script_path = _blacken_filter_args_from_normalized(
        normalized_intervals,
        script_dir=output_path.parent,
    )
    extra_args = [
        "-map",
        "0:v:0",
        *filter_args,
        "-color_range",
        STANDARD_VIDEO_ENCODING.color_range,
        "-fpsmax",
        STANDARD_VIDEO_ENCODING.max_fps_arg(),
        "-movflags",
        "+faststart",
    ]
    _update_or_append_ffmpeg_arg(
        encoder_args,
        "-pix_fmt",
        STANDARD_VIDEO_ENCODING.pixel_format,
    )

    command = _build_filter_transcode_command(
        ffmpeg_executable=ffmpeg_executable,
        input_path=input_path,
        output_path=output_path,
        encoder_args=encoder_args,
        extra_args=extra_args,
    )

    logger.info(
        "Starting streamed outside-frame blackening: %s -> %s",
        input_path.name,
        output_path.name,
    )
    logger.debug("Outside blackening FFmpeg command: %s", " ".join(command))

    try:
        returncode, stderr_output = _run_ffmpeg_command(command)
        if returncode != 0:
            logger.error(
                "FFmpeg outside blackening failed for %s with return code %d.",
                input_path.name,
                returncode,
            )
            logger.error("FFmpeg stderr:\n%s", stderr_output)
            _delete_partial_output(output_path, reason="incomplete outside blackening")
            return None
        if not _transcode_output_is_valid(output_path):
            _delete_partial_output(output_path, reason="invalid outside blackening")
            return None
        return output_path
    except subprocess.TimeoutExpired:
        logger.error(
            "FFmpeg outside blackening timed out for %s after %ss.",
            input_path.name,
            FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
        )
        _delete_partial_output(output_path, reason="timed-out outside blackening")
        return None
    finally:
        if filter_script_path is not None:
            safe_unlink_file(filter_script_path, missing_ok=True)


def mask_video_to_roi_and_blacken_intervals(
    input_path: Path,
    output_path: Path,
    *,
    endo_roi: RoiBoxCore | object,
    intervals: Iterable[tuple[int, int]] = (),
    quality_mode: str = "balanced",
    force_cpu: bool = False,
) -> Optional[Path]:
    if not input_path.exists():
        logger.error("Input file not found for ROI masking: %s", input_path)
        return None

    raw_interval_list = list(intervals)
    interval_list = (
        _normalize_blacken_intervals(raw_interval_list) if raw_interval_list else []
    )

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        logger.error(
            "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        )
        return None

    ensure_directory(output_path.parent)

    encoder_args, _encoder_type = _build_encoder_args(
        quality_mode,
        fallback=False,
        custom_crf=None,
        encoder_type_override="cpu" if force_cpu else None,
    )
    if "-vf" in encoder_args:
        vf_index = encoder_args.index("-vf")
        del encoder_args[vf_index : vf_index + 2]

    filter_args, filter_script_path = _roi_mask_and_blacken_filter_args(
        endo_roi=endo_roi,
        intervals=interval_list,
        script_dir=output_path.parent,
    )
    extra_args = [
        "-map",
        "0:v:0",
        *filter_args,
        "-color_range",
        STANDARD_VIDEO_ENCODING.color_range,
        "-fpsmax",
        STANDARD_VIDEO_ENCODING.max_fps_arg(),
        "-movflags",
        "+faststart",
    ]
    _update_or_append_ffmpeg_arg(
        encoder_args,
        "-pix_fmt",
        STANDARD_VIDEO_ENCODING.pixel_format,
    )

    command = _build_filter_transcode_command(
        ffmpeg_executable=ffmpeg_executable,
        input_path=input_path,
        output_path=output_path,
        encoder_args=encoder_args,
        extra_args=extra_args,
    )

    logger.info(
        "Starting streamed ROI masking: %s -> %s",
        input_path.name,
        output_path.name,
    )
    logger.debug("ROI masking FFmpeg command: %s", " ".join(command))

    try:
        returncode, stderr_output = _run_ffmpeg_command(command)
        if returncode != 0:
            logger.error(
                "FFmpeg ROI masking failed for %s with return code %d.",
                input_path.name,
                returncode,
            )
            logger.error("FFmpeg stderr:\n%s", stderr_output)
            _delete_partial_output(output_path, reason="incomplete ROI masking")
            return None
        if not _transcode_output_is_valid(output_path):
            _delete_partial_output(output_path, reason="invalid ROI masking")
            return None
        return output_path
    except subprocess.TimeoutExpired:
        logger.error(
            "FFmpeg ROI masking timed out for %s after %ss.",
            input_path.name,
            FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
        )
        _delete_partial_output(output_path, reason="timed-out ROI masking")
        return None
    finally:
        if filter_script_path is not None:
            safe_unlink_file(filter_script_path, missing_ok=True)
