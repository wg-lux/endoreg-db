import json
import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
from tqdm import tqdm

logger = logging.getLogger("ffmpeg_wrapper")

# Global hardware acceleration cache
_nvenc_available = None
_preferred_encoder = None


@lru_cache(maxsize=1)
def _resolve_ffmpeg_executable() -> Optional[str]:
    """Locate the ffmpeg executable using multiple discovery strategies."""
    # 1) Explicit overrides via env vars
    env_candidates = [
        os.environ.get("FFMPEG_EXECUTABLE"),
        os.environ.get("FFMPEG_BINARY"),
        os.environ.get("FFMPEG_PATH"),
    ]

    # 2) Django settings overrides (if Django is configured)
    try:
        from django.conf import settings

        env_candidates.extend(getattr(settings, attr) for attr in ("FFMPEG_EXECUTABLE", "FFMPEG_BINARY", "FFMPEG_PATH") if hasattr(settings, attr))
    except Exception:
        # Django might not be configured for every consumer
        pass

    # Normalize and verify explicit candidates
    for candidate in env_candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.is_dir():
            candidate_path = candidate_path / "ffmpeg"
        if candidate_path.exists() and os.access(candidate_path, os.X_OK):
            logger.debug("Using ffmpeg executable override at %s", candidate_path)
            return str(candidate_path)

    # 3) PATH lookup (shutil.which)
    via_path = shutil.which("ffmpeg")
    if via_path:
        return via_path

    # 4) Common fallback locations (useful for Nix-based environments)
    nix_store = Path("/nix/store")
    if nix_store.exists():
        patterns = (
            "*-ffmpeg-*/bin/ffmpeg",
            "*-ffmpeg-headless-*/bin/ffmpeg",
            "*-ffmpeg-headless*/bin/ffmpeg",
        )
        for pattern in patterns:
            matches = sorted(nix_store.glob(pattern))
            if matches:
                logger.debug("Discovered ffmpeg in nix store at %s", matches[-1])
                return str(matches[-1])

    # 5) Final fallback to standard Unix locations
    for fallback in (Path("/usr/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
        if fallback.exists() and os.access(fallback, os.X_OK):
            return str(fallback)

    return None


def _detect_nvenc_support() -> bool:
    """
    Detect if NVIDIA NVENC hardware acceleration is available.

    Returns:
        True if NVENC is available, False otherwise
    """
    try:
        # Test NVENC availability with a minimal command (minimum size for NVENC)
        cmd = ["ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=1:size=256x256:rate=1", "-c:v", "h264_nvenc", "-preset", "p1", "-f", "null", "-"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)

        if result.returncode == 0:
            logger.debug("NVENC h264 encoding test successful")
            return True
        else:
            logger.debug(f"NVENC test failed: {result.stderr}")
            return False

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug(f"NVENC detection failed: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error during NVENC detection: {e}")
        return False


def _get_preferred_encoder() -> Dict[str, str]:
    """
    Get the preferred video encoder configuration based on available hardware.

    Returns:
        Dictionary with encoder configuration
    """
    global _nvenc_available, _preferred_encoder

    if _nvenc_available is None:
        _nvenc_available = _detect_nvenc_support()

    if _preferred_encoder is None:
        if _nvenc_available:
            _preferred_encoder = {
                "name": "h264_nvenc",
                "preset_param": "-preset",
                "preset_value": "p4",  # Medium quality/speed for NVENC
                "quality_param": "-cq",
                "quality_value": "20",  # NVENC CQ mode
                "type": "nvenc",
                "fallback_preset": "p1",  # Fastest NVENC preset for fallback
            }
            logger.info("Hardware acceleration: NVENC available")
        else:
            _preferred_encoder = {
                "name": "libx264",
                "preset_param": "-preset",
                "preset_value": "medium",  # CPU preset
                "quality_param": "-crf",
                "quality_value": "23",  # CPU CRF mode
                "type": "cpu",
                "fallback_preset": "ultrafast",  # Fastest CPU preset for fallback
            }
            logger.info("Hardware acceleration: NVENC not available, using CPU")

    return _preferred_encoder


def _build_encoder_args(quality_mode: str = "balanced", fallback: bool = False, custom_crf: Optional[int] = None) -> Tuple[List[str], str]:
    """
    Build encoder command arguments based on available hardware and quality requirements.

    Args:
        quality_mode: 'fast', 'balanced', or 'quality'
        fallback: Whether to use fallback settings for compatibility
        custom_crf: Override quality setting (for backward compatibility)

    Returns:
        Tuple of (encoder_args, encoder_type)
    """
    encoder = _get_preferred_encoder()

    if encoder["type"] == "nvenc":
        # NVIDIA NVENC configuration
        if fallback:
            preset = encoder["fallback_preset"]  # p1 - fastest
            quality = "28"  # Lower quality for speed
        elif quality_mode == "fast":
            preset = "p2"  # Faster preset
            quality = "25"
        elif quality_mode == "quality":
            preset = "p6"  # Higher quality preset
            quality = "18"
        else:  # balanced
            preset = encoder["preset_value"]  # p4
            quality = encoder["quality_value"]  # 20

        # Override with custom CRF if provided (for backward compatibility)
        if custom_crf is not None:
            quality = str(custom_crf)

        return [
            "-c:v",
            encoder["name"],
            encoder["preset_param"],
            preset,
            encoder["quality_param"],
            quality,
            "-gpu",
            "0",  # Use first GPU
            "-rc",
            "vbr",  # Variable bitrate
            "-profile:v",
            "high",
        ], encoder["type"]
    else:
        # CPU libx264 configuration
        if fallback:
            preset = encoder["fallback_preset"]  # ultrafast
            quality = "28"  # Lower quality for speed
        elif quality_mode == "fast":
            preset = "faster"
            quality = "20"
        elif quality_mode == "quality":
            preset = "slow"
            quality = "18"
        else:  # balanced
            preset = encoder["preset_value"]  # medium
            quality = encoder["quality_value"]  # 23

        # Override with custom CRF if provided (for backward compatibility)
        if custom_crf is not None:
            quality = str(custom_crf)

        return ["-c:v", encoder["name"], encoder["preset_param"], preset, encoder["quality_param"], quality, "-profile:v", "high"], encoder["type"]


def is_ffmpeg_available() -> bool:
    """
    Checks whether the FFmpeg executable is available in the system's PATH.

    Returns:
        True if FFmpeg is found in the PATH; otherwise, False.
    """
    return _resolve_ffmpeg_executable() is not None


def check_ffmpeg_availability():
    """
    Verifies that FFmpeg is installed and available in the system's PATH.

    Raises:
        FileNotFoundError: If FFmpeg is not found.

    Returns:
        True if FFmpeg is available.
    """
    if not is_ffmpeg_available():
        error_msg = "FFmpeg is not available. Please install it and ensure it's in your PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)
    # logger.info("FFmpeg is available.") # Caller can log if needed
    return True


def get_stream_info(file_path: Path) -> Optional[Dict]:
    """
    Retrieves video stream information from a file using ffprobe.

    Runs ffprobe to extract stream metadata in JSON format from the specified video file. Returns a dictionary with stream information, or None if the file does not exist or if an error occurs during execution or parsing.
    """
    if not file_path.exists():
        logger.error("File not found for ffprobe: %s", file_path)
        return None

    command = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        str(file_path),
    ]
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


def assemble_video_from_frames(  # Renamed from assemble_video
    frame_paths: List[Path],
    output_path: Path,
    fps: float,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Optional[Path]:
    """
    Assembles a video from a list of frame image paths using cv2.VideoWriter.
    Determines dimensions from the first frame if not provided.
    """
    if not frame_paths:
        logger.error("No frame paths provided for video assembly.")
        return None

    if width is None or height is None:
        try:
            first_frame = cv2.imread(str(frame_paths[0]))
            if first_frame is None:
                raise IOError(f"Could not read first frame: {frame_paths[0]}")
            height, width, _ = first_frame.shape
            logger.info("Determined video dimensions from first frame: %dx%d", width, height)
        except Exception as e:
            logger.error("Error reading first frame to determine dimensions: %s", e, exc_info=True)
            return None

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    if not video_writer.isOpened():
        logger.error("Could not open video writer for path: %s", output_path)
        return None

    logger.info("Assembling video %s from %d frames...", output_path.name, len(frame_paths))
    try:
        for frame_path in tqdm(frame_paths, desc=f"Assembling {output_path.name}"):
            frame = cv2.imread(str(frame_path))
            if frame is None:
                logger.warning("Could not read frame %s, skipping.", frame_path)
                continue
            # Ensure frame dimensions match - resize if necessary (or log error)
            if frame.shape[1] != width or frame.shape[0] != height:
                logger.warning(f"Frame {frame_path} has dimensions {frame.shape[1]}x{frame.shape[0]}, expected {width}x{height}. Resizing.")
                frame = cv2.resize(frame, (width, height))
            video_writer.write(frame)
    finally:
        video_writer.release()
        logger.info("Finished assembling video: %s", output_path)

    return output_path


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

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine encoder configuration
    if codec == "auto" or preset == "auto":
        if force_cpu:
            # Force CPU encoding
            encoder_args, encoder_type = _build_encoder_args(quality_mode, fallback=False, custom_crf=crf)
            # Override to use CPU encoder
            encoder_args[1] = "libx264"  # Replace encoder name
            encoder_args[3] = "medium" if preset == "auto" else preset  # Replace preset
            if crf is not None:
                encoder_args[5] = str(crf)  # Replace quality value
        else:
            # Use automatic hardware detection
            encoder_args, encoder_type = _build_encoder_args(quality_mode, fallback=False, custom_crf=crf)
    else:
        # Manual codec/preset specification (backward compatibility)
        encoder_args = [
            "-c:v",
            codec,
            "-preset",
            preset,
            "-crf" if codec == "libx264" else "-cq",
            str(crf if crf is not None else 23),
        ]
        encoder_type = "nvenc" if "nvenc" in codec else "cpu"

    # Build complete command
    command = [
        "ffmpeg",
        "-i",
        str(input_path),
        *encoder_args,
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        "-y",  # Overwrite output file if it exists
    ]

    if extra_args:
        command.extend(extra_args)
    command.append(str(output_path))

    logger.info("Starting transcoding: %s -> %s (using %s)", input_path.name, output_path.name, encoder_type)
    logger.debug("FFmpeg command: %s", " ".join(command))

    try:
        process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True, universal_newlines=True)

        # Progress reporting and error handling
        stderr_output = ""
        if process.stderr:
            for line in process.stderr:
                stderr_output += line

        process.wait()

        if process.returncode == 0:
            logger.info("Transcoding finished successfully: %s", output_path)
            return output_path
        else:
            logger.error("FFmpeg transcoding failed for %s with return code %d.", input_path.name, process.returncode)
            logger.error("FFmpeg stderr:\n%s", stderr_output)

            # Try fallback to CPU if NVENC failed
            if encoder_type == "nvenc" and not force_cpu:
                logger.warning("NVENC transcoding failed, trying CPU fallback...")
                return _transcode_video_fallback(input_path, output_path, audio_codec, audio_bitrate, extra_args, quality_mode, crf)

            # Clean up potentially corrupted output file
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError as e:
                    logger.error("Failed to delete incomplete output file %s: %s", output_path, e)
            return None

    except FileNotFoundError:
        logger.error("ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH.")
        return None
    except Exception as e:
        logger.error("Error during transcoding of %s: %s", input_path.name, e, exc_info=True)
        return None


def _transcode_video_fallback(
    input_path: Path, output_path: Path, audio_codec: str, audio_bitrate: str, extra_args: Optional[List[str]], quality_mode: str, custom_crf: Optional[int]
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
        # Build CPU encoder arguments
        encoder_args, _ = _build_encoder_args(quality_mode, fallback=True, custom_crf=custom_crf)
        # Force CPU encoder
        encoder_args[1] = "libx264"

        command = [
            "ffmpeg",
            "-i",
            str(input_path),
            *encoder_args,
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            "-y",
        ]

        if extra_args:
            command.extend(extra_args)
        command.append(str(output_path))

        logger.info("CPU fallback transcoding: %s -> %s", input_path.name, output_path.name)
        logger.debug("Fallback FFmpeg command: %s", " ".join(command))

        process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True, universal_newlines=True)
        stderr_output = ""
        if process.stderr:
            for line in process.stderr:
                stderr_output += line

        process.wait()

        if process.returncode == 0:
            logger.info("CPU fallback transcoding successful: %s", output_path)
            return output_path
        else:
            logger.error("CPU fallback transcoding also failed for %s", input_path.name)
            logger.error("Fallback stderr:\n%s", stderr_output)
            return None

    except Exception as e:
        logger.error("Error during CPU fallback transcoding: %s", e, exc_info=True)
        return None

    logger.info("Starting transcoding: %s -> %s", input_path.name, output_path.name)
    logger.debug("FFmpeg command: %s", " ".join(command))

    try:
        process = subprocess.Popen(command, stderr=subprocess.PIPE, text=True, universal_newlines=True)

        # Optional: Progress reporting (can be complex to parse ffmpeg output reliably)
        # For simplicity, just wait and check the return code
        stderr_output = ""
        if process.stderr:
            for line in process.stderr:
                stderr_output += line
                # Simple progress indication or detailed logging
                # logger.debug(f"ffmpeg: {line.strip()}")

        process.wait()

        if process.returncode == 0:
            logger.info("Transcoding finished successfully: %s", output_path)
            return output_path
        else:
            logger.error("FFmpeg transcoding failed for %s with return code %d.", input_path.name, process.returncode)
            logger.error("FFmpeg stderr:\n%s", stderr_output)
            # Clean up potentially corrupted output file
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError as e:
                    logger.error("Failed to delete incomplete output file %s: %s", output_path, e)
            return None

    except FileNotFoundError:
        logger.error("ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH.")
        return None
    except Exception as e:
        logger.error("Error during transcoding of %s: %s", input_path.name, e, exc_info=True)
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
        logger.error("Could not get stream info for %s to check if transcoding is required.", input_path)
        return None

    video_stream = next((s for s in stream_info["streams"] if s.get("codec_type") == "video"), None)

    if not video_stream:
        logger.error("No video stream found in %s.", input_path)
        return None

    codec_name = video_stream.get("codec_name")
    pixel_format = video_stream.get("pix_fmt")
    # Check color range as well, default is usually 'tv' (limited)
    color_range = video_stream.get("color_range", "tv")  # Default to tv if not specified

    needs_transcoding = False
    transcode_reason = []
    if codec_name != required_codec:
        reason = f"Codec mismatch ({codec_name} != {required_codec})"
        logger.info("%s for %s. Transcoding required.", reason, input_path.name)
        transcode_reason.append(reason)
        needs_transcoding = True
    # Check both pixel format and color range for yuv420p
    if pixel_format != required_pixel_format or (pixel_format == "yuv420p" and color_range != "pc"):
        reason = f"Pixel format/color range mismatch (pix_fmt: {pixel_format}, color_range: {color_range} != {required_pixel_format} with color_range=pc)"
        logger.info("%s for %s. Transcoding required.", reason, input_path.name)
        transcode_reason.append(reason)
        needs_transcoding = True

    if needs_transcoding:
        logger.info("Transcoding %s to %s due to: %s", input_path.name, output_path.name, "; ".join(transcode_reason))
        # Ensure codec and pixel format are set in options if not already present
        transcode_options.setdefault("codec", "libx264" if required_codec == "h264" else required_codec)
        transcode_options.setdefault("extra_args", [])

        # Ensure pixel format and color range are correctly set in extra_args
        extra_args = transcode_options["extra_args"]
        if "-pix_fmt" not in extra_args:
            extra_args.extend(["-pix_fmt", required_pixel_format])
        else:
            # If pix_fmt is already set, ensure it's the required one
            try:
                pix_fmt_index = extra_args.index("-pix_fmt")
                if extra_args[pix_fmt_index + 1] != required_pixel_format:
                    logger.warning("Overriding existing -pix_fmt '%s' with '%s'", extra_args[pix_fmt_index + 1], required_pixel_format)
                    extra_args[pix_fmt_index + 1] = required_pixel_format
            except (ValueError, IndexError):
                # Should not happen if '-pix_fmt' is in extra_args, but handle defensively
                logger.error("Error processing existing -pix_fmt argument. Appending required format.")
                extra_args.extend(["-pix_fmt", required_pixel_format])

        if "-color_range" not in extra_args:
            # Add color range 'pc' (which corresponds to 2 or 'jpeg') for yuv420p
            extra_args.extend(["-color_range", "pc"])
        else:
            # If color_range is already set, ensure it's 'pc'
            try:
                color_range_index = extra_args.index("-color_range")
                if extra_args[color_range_index + 1] != "pc":
                    logger.warning("Overriding existing -color_range '%s' with 'pc'", extra_args[color_range_index + 1])
                    extra_args[color_range_index + 1] = "pc"
            except (ValueError, IndexError):
                logger.error("Error processing existing -color_range argument. Appending 'pc'.")
                extra_args.extend(["-color_range", "pc"])

        return transcode_video(input_path, output_path, **transcode_options)
    else:
        logger.info(
            "Video %s already meets requirements (%s, %s, color_range=pc). No transcoding needed.", input_path.name, required_codec, required_pixel_format
        )
        # If no transcoding is needed, should we copy/link or just return the original path?
        # For simplicity, let's assume the caller handles the file location.
        # If the output_path is different, we might need to copy.
        if input_path != output_path:
            # Example: copy file if output path is different
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(input_path, output_path)
                logger.info("Copied %s to %s as it met requirements.", input_path.name, output_path.name)
                return output_path
            except Exception as e:
                logger.error("Failed to copy %s to %s: %s", input_path.name, output_path.name, e)
                return None
        return input_path  # Return original path if no copy needed


def extract_frames(video_path: Path, output_dir: Path, quality: int, ext: str = "jpg", fps: Optional[float] = None) -> List[Path]:
    """
    Extracts frames from a video file using FFmpeg.

    Args:
        video_path: Path to the input video file.
        output_dir: Directory to save the extracted frames.
        quality: Quality factor for JPEG extraction (1-31, lower is better).
        ext: Output frame image extension (e.g., 'jpg', 'png').
        fps: Optional frames per second to extract. If None, extracts all frames.

    Returns:
        A list of Path objects for the extracted frames.
    """
    # Resolve ffmpeg executable with multiple fallbacks
    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        error_msg = "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = output_dir / f"frame_%07d.{ext}"

    cmd = [
        ffmpeg_executable,  # Use the found executable path
        "-i",
        str(video_path),
        "-qscale:v",
        str(quality),  # Video quality scale
    ]

    if fps is not None:
        cmd.extend(["-vf", f"fps={fps}"])

    cmd.append(str(output_pattern))

    logger.info("Running FFmpeg command: %s", " ".join(cmd))
    try:
        # Use subprocess.run for better error handling
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.debug("FFmpeg stdout:\n%s", result.stdout)
        logger.debug("FFmpeg stderr:\n%s", result.stderr)
        logger.info("FFmpeg frame extraction completed successfully.")
    except FileNotFoundError as exc:
        # This might be redundant now but kept for safety
        error_msg = f"ffmpeg command not found at '{ffmpeg_executable}'. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg) from exc
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg command failed with exit code %d.", e.returncode)
        logger.error("FFmpeg stderr:\n%s", e.stderr)
        logger.error("FFmpeg stdout:\n%s", e.stdout)
        # Return empty list on error as frames were likely not created correctly
        return []
    except Exception as e:
        logger.error("An unexpected error occurred during FFmpeg execution: %s", e, exc_info=True)
        return []

    # Collect paths of extracted frames
    extracted_files = sorted(output_dir.glob(f"frame_*.{ext}"))
    return extracted_files


def extract_frame_range(
    video_path: Path,
    output_dir: Path,
    start_frame: int,
    end_frame: int,  # Exclusive end frame number
    quality: int,
    ext: str = "jpg",
) -> List[Path]:
    """
    Extracts a specific range of frames from a video using FFmpeg.

    Frames from start_frame (inclusive) to end_frame (exclusive) are saved as images
    in the output directory, following the naming pattern 'frame_%07d.ext'. The
    function ensures only the requested frames are returned, and cleans up partial
    results on failure.

    Args:
        video_path: Path to the input video file.
        output_dir: Directory where extracted frames will be saved.
        start_frame: Index of the first frame to extract (inclusive, 0-based).
        end_frame: Index at which to stop extraction (exclusive, 0-based).
        quality: JPEG quality factor (1-31, lower is better).
        ext: File extension for output images (e.g., 'jpg', 'png').

    Returns:
        List of Paths to the extracted frame image files within the specified range.

    Raises:
        FileNotFoundError: If the FFmpeg executable is not found.
        ValueError: If start_frame is greater than or equal to end_frame.
        RuntimeError: If FFmpeg fails to extract the requested frames.
    """
    if start_frame >= end_frame:
        logger.warning("extract_frame_range called with start_frame (%d) >= end_frame (%d). No frames to extract.", start_frame, end_frame)
        return []

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        error_msg = "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    output_dir.mkdir(parents=True, exist_ok=True)
    # Use a consistent naming convention, matching extract_frames
    output_pattern = output_dir / f"frame_%07d.{ext}"

    # Use select filter for precise frame range extraction
    # 'select' uses 0-based indexing 'n'
    # We want frames where start_frame <= n < end_frame
    select_filter = f"select='between(n,{start_frame},{end_frame - 1})'"

    cmd = [
        ffmpeg_executable,
        "-i",
        str(video_path),
        "-vf",
        select_filter,
        "-vsync",
        "vfr",  # Variable frame rate sync to handle selected frames
        "-qscale:v",
        str(quality),
        "-copyts",  # Attempt to copy timestamps if needed, might not be accurate with select
        str(output_pattern),
    ]

    logger.info("Running FFmpeg command for frame range extraction: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.debug("FFmpeg stdout:\n%s", result.stdout)
        logger.debug("FFmpeg stderr:\n%s", result.stderr)
        logger.info("FFmpeg frame range extraction completed successfully.")
    except FileNotFoundError as exc:
        error_msg = f"ffmpeg command not found at '{ffmpeg_executable}'. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg) from exc
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg command failed with exit code %d.", e.returncode)
        logger.error("FFmpeg stderr:\n%s", e.stderr)
        logger.error("FFmpeg stdout:\n%s", e.stdout)
        # Clean up potentially partially created files in the target directory within the expected range
        logger.warning("Attempting cleanup of potentially incomplete frames in %s", output_dir)
        for i in range(start_frame, end_frame):
            potential_file = output_dir / f"frame_{i:07d}.{ext}"
            if potential_file.exists():
                try:
                    potential_file.unlink()
                except OSError as unlink_err:
                    logger.error("Failed to delete potential frame %s during cleanup: %s", potential_file, unlink_err)
        raise RuntimeError(f"FFmpeg frame range extraction failed for {video_path}") from e
    except Exception as e:
        logger.error("An unexpected error occurred during FFmpeg execution: %s", e, exc_info=True)
        raise RuntimeError(f"Unexpected error during FFmpeg frame range extraction for {video_path}") from e

    # Collect paths of extracted frames matching the pattern and expected range
    # FFmpeg might create files outside the exact range depending on version/flags,
    # so filter explicitly.
    extracted_files = []
    for i in range(start_frame, end_frame):
        frame_file = output_dir / f"frame_{i:07d}.{ext}"
        if frame_file.exists():
            extracted_files.append(frame_file)
        else:
            # This might happen if ffmpeg fails silently for some frames or if the video ends early.
            logger.warning("Expected frame file %s not found after extraction.", frame_file)

    logger.info("Found %d extracted frame files in range [%d, %d) for video %s.", len(extracted_files), start_frame, end_frame, video_path.name)
    return extracted_files


__all__ = [
    "is_ffmpeg_available",  # ADDED
    "check_ffmpeg_availability",  # ADDED
    "get_stream_info",
    "assemble_video_from_frames",  # Updated name
    "transcode_video",
    "transcode_videofile_if_required",
    "extract_frames",
    "extract_frame_range",  # Add new function to __all__
]
