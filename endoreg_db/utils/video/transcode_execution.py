import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

from endoreg_db.config.env import get_ffmpeg_transcode_timeout_seconds
from endoreg_db.utils.file_operations import (
    atomic_copy_file,
    ensure_directory,
    safe_unlink_file,
)

from .command_construction import (
    TimestampRepairMode,
    _TIMESTAMP_REPAIR_SEQUENCE,
    _build_ffprobe_stream_info_command,
    _build_transcode_command,
    _update_or_append_ffmpeg_arg,
)
from .encoder_policy import _build_encoder_args
from .executable_discovery import (
    _resolve_ffmpeg_executable,
    _resolve_ffprobe_executable,
)

logger = logging.getLogger("ffmpeg_wrapper")
FFMPEG_TRANSCODE_TIMEOUT_SECONDS = get_ffmpeg_transcode_timeout_seconds()


def _delete_partial_output(output_path: Path, *, reason: str) -> None:
    """Remove an ffmpeg output file that may be incomplete or corrupt."""
    if not output_path.exists():
        return
    try:
        safe_unlink_file(output_path, missing_ok=True)
    except OSError as e:
        logger.error("Failed to delete %s output file %s: %s", reason, output_path, e)


def _transcode_output_is_valid(output_path: Path) -> bool:
    if not output_path.exists():
        logger.error("FFmpeg reported success but output is missing: %s", output_path)
        return False
    if output_path.stat().st_size <= 0:
        logger.error("FFmpeg reported success but output is empty: %s", output_path)
        return False
    return True


def _stderr_indicates_timestamp_fault(stderr_output: str) -> bool:
    normalized = stderr_output.lower()
    if not normalized:
        return False
    has_timestamp_signal = any(
        pattern in normalized for pattern in ("timestamp", "pts", "dts")
    )
    has_fault_signal = any(
        pattern in normalized
        for pattern in (
            "invalid",
            "too large",
            "out of range",
            "non monotonically increasing",
            "overflow",
            "clipping",
        )
    )
    return has_timestamp_signal and has_fault_signal


def _run_ffmpeg_command(command: List[str]) -> Tuple[int, str]:
    """Run ffmpeg while preserving timeout behavior for long transcodes."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        universal_newlines=True,
    )
    try:
        _stdout, stderr_output = process.communicate(
            timeout=FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        process.kill()
        _stdout, stderr_output = process.communicate()
        cast(Any, exc).stderr = stderr_output
        raise exc

    return process.returncode, stderr_output or ""


def get_stream_info(file_path: Path) -> Optional[Dict]:
    """
    Retrieves video stream information from a file using ffprobe.

    Runs ffprobe to extract stream metadata in JSON format from the specified video file. Returns a dictionary with stream information, or None if the file does not exist or if an error occurs during execution or parsing.
    """
    if not file_path.exists():
        logger.error("File not found for ffprobe: %s", file_path)
        return None

    ffprobe_executable = _resolve_ffprobe_executable()
    if not ffprobe_executable:
        logger.error(
            "ffprobe command not found. Ensure FFmpeg is installed and in the system's PATH."
        )
        return None

    command = _build_ffprobe_stream_info_command(
        ffprobe_executable=ffprobe_executable,
        file_path=file_path,
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        logger.error("ffprobe command failed for %s: %s\n%s", file_path, e, e.stderr)
        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse ffprobe JSON output for %s: %s", file_path, e)
        return None
    except Exception as e:
        logger.error("Error running ffprobe for %s: %s", file_path, e, exc_info=True)
        return None


def transcode_video(
    input_path: Path,
    output_path: Path,
    codec: str = "auto",  # Changed default to "auto" for automatic selection
    crf: Optional[int] = None,  # Will be determined automatically if None
    preset: str = "auto",  # Changed default to "auto" for automatic selection
    audio_codec: str = "aac",
    audio_bitrate: str = "128k",
    extra_args: Optional[List[str]] = None,
    quality_mode: str = "balanced",  # New parameter: 'fast', 'balanced', 'quality'
    force_cpu: bool = False,  # New parameter to force CPU encoding
) -> Optional[Path]:
    """
    Transcodes a video file using FFmpeg with automatic hardware acceleration.

    Args:
        input_path: Source video file path
        output_path: Output video file path
        codec: Video codec ('auto' for automatic selection, 'libx264', 'h264_nvenc')
        crf: Constant Rate Factor (None for automatic selection)
        preset: Encoder preset ('auto' for automatic selection)
        audio_codec: Audio codec
        audio_bitrate: Audio bitrate
        extra_args: Additional FFmpeg arguments
        quality_mode: Quality mode ('fast', 'balanced', 'quality')
        force_cpu: Force CPU encoding even if NVENC is available

    Returns:
        Path to transcoded video or None if failed
    """
    if not input_path.exists():
        logger.error("Input file not found for transcoding: %s", input_path)
        return None

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        logger.error(
            "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        )
        return None

    ensure_directory(output_path.parent)

    # Determine encoder configuration
    if codec == "auto" or preset == "auto":
        if force_cpu:
            encoder_args, encoder_type = _build_encoder_args(
                quality_mode,
                fallback=False,
                custom_crf=crf,
                encoder_type_override="cpu",
            )
        else:
            # Use automatic hardware detection
            encoder_args, encoder_type = _build_encoder_args(
                quality_mode, fallback=False, custom_crf=crf
            )
    else:
        # Manual codec/preset specification (backward compatibility)
        encoder_args = [
            "-c",
            codec,
            "-preset",
            preset,
            "-crf" if codec == "libx264" else "-cq",
            str(crf if crf is not None else 23),
        ]
        encoder_type = "nvenc" if "nvenc" in codec else "cpu"

    command = _build_transcode_command(
        ffmpeg_executable=ffmpeg_executable,
        input_path=input_path,
        output_path=output_path,
        encoder_args=encoder_args,
        audio_codec=audio_codec,
        audio_bitrate=audio_bitrate,
        extra_args=extra_args,
        timestamp_repair_mode=TimestampRepairMode.NONE,
    )

    logger.info(
        "Starting transcoding: %s -> %s (using %s)",
        input_path.name,
        output_path.name,
        encoder_type,
    )
    logger.debug("FFmpeg command: %s", " ".join(command))

    try:
        returncode, stderr_output = _run_ffmpeg_command(command)

        if returncode == 0:
            if not _transcode_output_is_valid(output_path):
                _delete_partial_output(output_path, reason="invalid successful")
                return None
            logger.info("Transcoding finished successfully: %s", output_path)
            return output_path
        else:
            logger.error(
                "FFmpeg transcoding failed for %s with return code %d.",
                input_path.name,
                returncode,
            )
            logger.error("FFmpeg stderr:\n%s", stderr_output)

            if _stderr_indicates_timestamp_fault(stderr_output):
                logger.warning(
                    "FFmpeg reported corrupt timestamps for %s; "
                    "retrying with timestamp repair.",
                    input_path.name,
                )
                repaired_result = _transcode_video_with_timestamp_repair(
                    ffmpeg_executable=ffmpeg_executable,
                    input_path=input_path,
                    output_path=output_path,
                    encoder_args=encoder_args,
                    encoder_type=encoder_type,
                    audio_codec=audio_codec,
                    audio_bitrate=audio_bitrate,
                    extra_args=extra_args,
                    quality_mode=quality_mode,
                    custom_crf=crf,
                    force_cpu=force_cpu,
                )
                if repaired_result is not None:
                    return repaired_result

            # Try fallback to CPU if NVENC failed
            if encoder_type == "nvenc" and not force_cpu:
                logger.warning("NVENC transcoding failed, trying CPU fallback...")
                return _transcode_video_fallback(
                    input_path,
                    output_path,
                    audio_codec,
                    audio_bitrate,
                    extra_args,
                    quality_mode,
                    crf,
                )

            _delete_partial_output(output_path, reason="incomplete")
            return None
    except subprocess.TimeoutExpired:
        logger.error(
            "FFmpeg transcoding timed out for %s after %ss.",
            input_path.name,
            FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
        )
        _delete_partial_output(output_path, reason="timed-out")
        return None

    except FileNotFoundError:
        logger.error(
            "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        )
        return None
    except Exception as e:
        logger.error(
            "Error during transcoding of %s: %s", input_path.name, e, exc_info=True
        )
        return None


def _transcode_video_with_timestamp_repair(
    *,
    ffmpeg_executable: str,
    input_path: Path,
    output_path: Path,
    encoder_args: List[str],
    encoder_type: str,
    audio_codec: str,
    audio_bitrate: str,
    extra_args: Optional[List[str]],
    quality_mode: str,
    custom_crf: Optional[int],
    force_cpu: bool,
) -> Optional[Path]:
    for mode in _TIMESTAMP_REPAIR_SEQUENCE:
        _delete_partial_output(
            output_path,
            reason=f"before timestamp repair {mode.value}",
        )
        command = _build_transcode_command(
            ffmpeg_executable=ffmpeg_executable,
            input_path=input_path,
            output_path=output_path,
            encoder_args=encoder_args,
            audio_codec=audio_codec,
            audio_bitrate=audio_bitrate,
            extra_args=extra_args,
            timestamp_repair_mode=mode,
        )
        logger.info(
            "Retrying FFmpeg transcode for %s with timestamp repair mode %s.",
            input_path.name,
            mode.value,
        )
        logger.debug("Timestamp repair FFmpeg command: %s", " ".join(command))
        try:
            returncode, stderr_output = _run_ffmpeg_command(command)
        except subprocess.TimeoutExpired:
            logger.error(
                "Timestamp repair transcode timed out for %s with mode %s after %ss.",
                input_path.name,
                mode.value,
                FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
            )
            _delete_partial_output(output_path, reason="timed-out timestamp repair")
            continue

        if returncode == 0:
            if not _transcode_output_is_valid(output_path):
                _delete_partial_output(
                    output_path,
                    reason=f"invalid timestamp repair {mode.value}",
                )
                continue
            logger.info(
                "Timestamp repair transcode succeeded for %s with mode %s.",
                input_path.name,
                mode.value,
            )
            return output_path

        logger.warning(
            "Timestamp repair mode %s failed for %s with return code %d.",
            mode.value,
            input_path.name,
            returncode,
        )
        logger.debug("Timestamp repair stderr:\n%s", stderr_output)

    if encoder_type == "nvenc" and not force_cpu:
        logger.warning(
            "Timestamp repair with NVENC failed for %s; trying CPU timestamp repair.",
            input_path.name,
        )
        cpu_encoder_args, _ = _build_encoder_args(
            quality_mode,
            fallback=True,
            custom_crf=custom_crf,
            encoder_type_override="cpu",
        )
        for mode in _TIMESTAMP_REPAIR_SEQUENCE:
            _delete_partial_output(
                output_path,
                reason=f"before CPU timestamp repair {mode.value}",
            )
            command = _build_transcode_command(
                ffmpeg_executable=ffmpeg_executable,
                input_path=input_path,
                output_path=output_path,
                encoder_args=cpu_encoder_args,
                audio_codec=audio_codec,
                audio_bitrate=audio_bitrate,
                extra_args=extra_args,
                timestamp_repair_mode=mode,
            )
            logger.info(
                "Retrying CPU FFmpeg transcode for %s with timestamp repair mode %s.",
                input_path.name,
                mode.value,
            )
            try:
                returncode, stderr_output = _run_ffmpeg_command(command)
            except subprocess.TimeoutExpired:
                logger.error(
                    "CPU timestamp repair transcode timed out for %s with mode %s after %ss.",
                    input_path.name,
                    mode.value,
                    FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
                )
                _delete_partial_output(
                    output_path,
                    reason="timed-out CPU timestamp repair",
                )
                continue

            if returncode == 0:
                if not _transcode_output_is_valid(output_path):
                    _delete_partial_output(
                        output_path,
                        reason=f"invalid CPU timestamp repair {mode.value}",
                    )
                    continue
                logger.info(
                    "CPU timestamp repair transcode succeeded for %s with mode %s.",
                    input_path.name,
                    mode.value,
                )
                return output_path
            logger.warning(
                "CPU timestamp repair mode %s failed for %s with return code %d.",
                mode.value,
                input_path.name,
                returncode,
            )
            logger.debug("CPU timestamp repair stderr:\n%s", stderr_output)

    _delete_partial_output(output_path, reason="failed timestamp repair")
    return None


def _transcode_video_fallback(
    input_path: Path,
    output_path: Path,
    audio_codec: str,
    audio_bitrate: str,
    extra_args: Optional[List[str]],
    quality_mode: str,
    custom_crf: Optional[int],
) -> Optional[Path]:
    """
    Fallback transcoding using CPU encoding.

    Args:
        input_path: Source video file path
        output_path: Output video file path
        audio_codec: Audio codec
        audio_bitrate: Audio bitrate
        extra_args: Additional FFmpeg arguments
        quality_mode: Quality mode
        custom_crf: Custom CRF value

    Returns:
        Path to transcoded video or None if failed
    """
    try:
        ffmpeg_executable = _resolve_ffmpeg_executable()
        if not ffmpeg_executable:
            logger.error(
                "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
            )
            return None

        # Build CPU encoder arguments without carrying over NVENC-only options.
        encoder_args, _ = _build_encoder_args(
            quality_mode,
            fallback=True,
            custom_crf=custom_crf,
            encoder_type_override="cpu",
        )

        command = _build_transcode_command(
            ffmpeg_executable=ffmpeg_executable,
            input_path=input_path,
            output_path=output_path,
            encoder_args=encoder_args,
            audio_codec=audio_codec,
            audio_bitrate=audio_bitrate,
            extra_args=extra_args,
            timestamp_repair_mode=TimestampRepairMode.NONE,
        )

        logger.info(
            "CPU fallback transcoding: %s -> %s", input_path.name, output_path.name
        )
        logger.debug("Fallback FFmpeg command: %s", " ".join(command))

        returncode, stderr_output = _run_ffmpeg_command(command)

        if returncode == 0:
            if not _transcode_output_is_valid(output_path):
                _delete_partial_output(output_path, reason="invalid CPU fallback")
                return None
            logger.info("CPU fallback transcoding successful: %s", output_path)
            return output_path
        else:
            logger.error("CPU fallback transcoding also failed for %s", input_path.name)
            logger.error("Fallback stderr:\n%s", stderr_output)
            _delete_partial_output(output_path, reason="incomplete")
            return None

    except subprocess.TimeoutExpired:
        logger.error(
            "CPU fallback transcoding timed out for %s after %ss.",
            input_path.name,
            FFMPEG_TRANSCODE_TIMEOUT_SECONDS,
        )
        _delete_partial_output(output_path, reason="timed-out")
        return None
    except Exception as e:
        logger.error("Error during CPU fallback transcoding: %s", e, exc_info=True)
        return None


def transcode_videofile_if_required(
    input_path: Path,
    output_path: Path,
    required_codec: str = "h264",
    required_pixel_format: str = "yuv420p",  # Changed default from yuvj420p
    **transcode_options,  # Pass other options to transcode_video
) -> Optional[Path]:
    """
    Checks if a video needs transcoding based on codec and pixel format,
    and transcodes it using transcode_video if necessary.
    Uses yuv420p with full color range (pc/jpeg) as the target format.
    Returns the path to the compliant video (original or transcoded).
    """
    stream_info = get_stream_info(input_path)
    if not stream_info or "streams" not in stream_info:
        logger.error(
            "Could not get stream info for %s to check if transcoding is required.",
            input_path,
        )
        return None

    video_stream = next(
        (s for s in stream_info["streams"] if s.get("codec_type") == "video"), None
    )

    if not video_stream:
        logger.error("No video stream found in %s.", input_path)
        return None

    codec_name = video_stream.get("codec_name")
    pixel_format = video_stream.get("pix_fmt")
    # Check color range as well, default is usually 'tv' (limited)
    color_range = video_stream.get(
        "color_range", "tv"
    )  # Default to tv if not specified

    needs_transcoding = False
    transcode_reason = []
    if codec_name != required_codec:
        reason = f"Codec mismatch ({codec_name} != {required_codec})"
        logger.info("%s for %s. Transcoding required.", reason, input_path.name)
        transcode_reason.append(reason)
        needs_transcoding = True
    # Check both pixel format and color range for yuv420p
    if pixel_format != required_pixel_format or (
        pixel_format == "yuv420p" and color_range != "pc"
    ):
        reason = f"Pixel format/color range mismatch (pix_fmt: {pixel_format}, color_range: {color_range} != {required_pixel_format} with color_range=pc)"
        logger.info("%s for %s. Transcoding required.", reason, input_path.name)
        transcode_reason.append(reason)
        needs_transcoding = True

    if needs_transcoding:
        logger.info(
            "Transcoding %s to %s due to: %s",
            input_path.name,
            output_path.name,
            "; ".join(transcode_reason),
        )
        # Ensure codec and pixel format are set in options if not already present
        transcode_options.setdefault(
            "codec", "libx264" if required_codec == "h264" else required_codec
        )
        transcode_options.setdefault("extra_args", [])

        # Ensure pixel format and color range are correctly set in extra_args
        extra_args = transcode_options["extra_args"]
        _update_or_append_ffmpeg_arg(extra_args, "-pix_fmt", required_pixel_format)
        # Add color range 'pc' (which corresponds to 2 or 'jpeg') for yuv420p
        _update_or_append_ffmpeg_arg(extra_args, "-color_range", "pc")

        return transcode_video(input_path, output_path, **transcode_options)
    else:
        logger.info(
            "Video %s already meets requirements (%s, %s, color_range=pc). No transcoding needed.",
            input_path.name,
            required_codec,
            required_pixel_format,
        )
        # If no transcoding is needed, should we copy/link or just return the original path?
        # For simplicity, let's assume the caller handles the file location.
        # If the output_path is different, we might need to copy.
        if input_path != output_path:
            # Example: copy file if output path is different
            try:
                atomic_copy_file(source=input_path, destination=output_path)
                logger.info(
                    "Copied %s to %s as it met requirements.",
                    input_path.name,
                    output_path.name,
                )
                return output_path
            except Exception as e:
                logger.error(
                    "Failed to copy %s to %s: %s", input_path.name, output_path.name, e
                )
                return None
        return input_path  # Return original path if no copy needed
