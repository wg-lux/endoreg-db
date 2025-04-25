import subprocess
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
import cv2
from tqdm import tqdm
import shutil

logger = logging.getLogger("ffmpeg_wrapper")

def get_stream_info(file_path: Path) -> Optional[Dict]:
    """
    Runs ffprobe -show_streams, parses JSON, returns relevant stream data.
    """
    if not file_path.exists():
        logger.error("File not found for ffprobe: %s", file_path)
        return None

    command = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
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


def assemble_video_from_frames( # Renamed from assemble_video
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

    fourcc = cv2.VideoWriter_fourcc(*"mp4v") # type: ignore
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
    codec: str = "libx264",
    crf: int = 23,
    preset: str = "medium",
    audio_codec: str = "aac",
    audio_bitrate: str = "128k",
    extra_args: Optional[List[str]] = None,
) -> Optional[Path]:
    """
    Transcodes a video file using FFmpeg.
    """
    if not input_path.exists():
        logger.error("Input file not found for transcoding: %s", input_path)
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg",
        "-i", str(input_path),
        "-c:v", codec,
        "-crf", str(crf),
        "-preset", preset,
        "-c:a", audio_codec,
        "-b:a", audio_bitrate,
        "-y", # Overwrite output file if it exists
    ]
    if extra_args:
        command.extend(extra_args)
    command.append(str(output_path))

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
    required_pixel_format: str = "yuvj420p", # "juv420p"
    **transcode_options # Pass other options to transcode_video
) -> Optional[Path]:
    """
    Checks if a video needs transcoding based on codec and pixel format,
    and transcodes it using transcode_video if necessary.
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

    needs_transcoding = False
    if codec_name != required_codec:
        logger.info("Codec mismatch (%s != %s) for %s. Transcoding required.", codec_name, required_codec, input_path.name)
        needs_transcoding = True
    if pixel_format != required_pixel_format:
        logger.info("Pixel format mismatch (%s != %s) for %s. Transcoding required.", pixel_format, required_pixel_format, input_path.name)
        needs_transcoding = True

    if needs_transcoding:
        logger.info("Transcoding %s to %s...", input_path.name, output_path.name)
        # Ensure codec and pixel format are set in options if not already present
        transcode_options.setdefault('codec', 'libx264' if required_codec == 'h264' else required_codec)
        transcode_options.setdefault('extra_args', [])
        # Add pix_fmt argument if not already handled by codec preset
        if '-pix_fmt' not in transcode_options['extra_args']:
            transcode_options['extra_args'].extend(['-pix_fmt', required_pixel_format])

        return transcode_video(input_path, output_path, **transcode_options)
    else:
        logger.info("Video %s already meets requirements. No transcoding needed.", input_path.name)
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
        return input_path # Return original path if no copy needed

def extract_frames(
    video_path: Path,
    output_dir: Path,
    quality: int,
    ext: str = "jpg",
    fps: Optional[float] = None
) -> List[Path]:
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
    # Check if ffmpeg command exists
    ffmpeg_executable = shutil.which("ffmpeg")
    if not ffmpeg_executable:
        error_msg = "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = output_dir / f"frame_%07d.{ext}"

    cmd = [
        ffmpeg_executable, # Use the found executable path
        "-i", str(video_path),
        "-qscale:v", str(quality), # Video quality scale
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
    end_frame: int, # Exclusive end frame number
    quality: int,
    ext: str = "jpg",
) -> List[Path]:
    """
    Extracts frames within a specific range [start_frame, end_frame) using FFmpeg.

    Args:
        video_path: Path to the input video file.
        output_dir: Directory to save the extracted frames.
        start_frame: The first frame number to extract (inclusive, 0-based).
        end_frame: The frame number to stop before (exclusive, 0-based).
        quality: Quality factor for JPEG extraction (1-31, lower is better).
        ext: Output frame image extension (e.g., 'jpg', 'png').

    Returns:
        A list of Path objects for the extracted frames.
    Raises:
        FileNotFoundError: If ffmpeg executable is not found.
        ValueError: If start_frame >= end_frame.
        RuntimeError: If the ffmpeg command fails.
    """
    if start_frame >= end_frame:
        logger.warning("extract_frame_range called with start_frame (%d) >= end_frame (%d). No frames to extract.", start_frame, end_frame)
        return []

    ffmpeg_executable = shutil.which("ffmpeg")
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
    select_filter = f"select='between(n,{start_frame},{end_frame-1})'"

    cmd = [
        ffmpeg_executable,
        "-i", str(video_path),
        "-vf", select_filter,
        "-vsync", "vfr", # Variable frame rate sync to handle selected frames
        "-qscale:v", str(quality),
        "-copyts", # Attempt to copy timestamps if needed, might not be accurate with select
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
    "get_stream_info",
    "assemble_video_from_frames", # Updated name
    "transcode_video",
    "transcode_videofile_if_required",
    "extract_frames",
    "extract_frame_range", # Add new function to __all__
]
