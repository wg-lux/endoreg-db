import logging
import subprocess
from pathlib import Path
from typing import List, Optional

import cv2
from tqdm import tqdm

from endoreg_db.utils.filesystem.file_operations import (
    ensure_directory,
    safe_unlink_file,
)

from .command_construction import (
    _build_extract_frame_range_command,
    _build_extract_frames_command,
)
from .executable_discovery import _resolve_ffmpeg_executable

logger = logging.getLogger("ffmpeg_wrapper")


def assemble_video_from_frames(
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
            logger.info(
                "Determined video dimensions from first frame: %dx%d", width, height
            )
        except Exception as e:
            logger.error(
                "Error reading first frame to determine dimensions: %s",
                e,
                exc_info=True,
            )
            return None

    fourcc = cv2.VideoWriter.fourcc(*"mp4v")
    ensure_directory(output_path.parent)
    video_writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    if not video_writer.isOpened():
        logger.error("Could not open video writer for path: %s", output_path)
        return None

    logger.info(
        "Assembling video %s from %d frames...", output_path.name, len(frame_paths)
    )
    try:
        for frame_path in tqdm(frame_paths, desc=f"Assembling {output_path.name}"):
            frame = cv2.imread(str(frame_path))
            if frame is None:
                logger.warning("Could not read frame %s, skipping.", frame_path)
                continue
            # Ensure frame dimensions match - resize if necessary (or log error)
            if frame.shape[1] != width or frame.shape[0] != height:
                logger.warning(
                    f"Frame {frame_path} has dimensions {frame.shape[1]}x{frame.shape[0]}, expected {width}x{height}. Resizing."
                )
                frame = cv2.resize(frame, (width, height))
            video_writer.write(frame)
    finally:
        video_writer.release()
        logger.info("Finished assembling video: %s", output_path)

    return output_path


def extract_frames(
    video_path: Path,
    output_dir: Path,
    quality: int,
    ext: str = "jpg",
    fps: Optional[float] = None,
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
    # Resolve ffmpeg executable with multiple fallbacks
    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        error_msg = "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    ensure_directory(output_dir)
    output_pattern = output_dir / f"frame_%07d.{ext}"

    cmd = _build_extract_frames_command(
        ffmpeg_executable=ffmpeg_executable,
        video_path=video_path,
        output_pattern=output_pattern,
        quality=quality,
        fps=fps,
    )

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
        logger.error(
            "An unexpected error occurred during FFmpeg execution: %s", e, exc_info=True
        )
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
        logger.warning(
            "extract_frame_range called with start_frame (%d) >= end_frame (%d). No frames to extract.",
            start_frame,
            end_frame,
        )
        return []

    ffmpeg_executable = _resolve_ffmpeg_executable()
    if not ffmpeg_executable:
        error_msg = "ffmpeg command not found. Ensure FFmpeg is installed and in the system's PATH."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    ensure_directory(output_dir)
    # Use a consistent naming convention, matching extract_frames
    output_pattern = output_dir / f"frame_%07d.{ext}"

    cmd = _build_extract_frame_range_command(
        ffmpeg_executable=ffmpeg_executable,
        video_path=video_path,
        output_pattern=output_pattern,
        start_frame=start_frame,
        end_frame=end_frame,
        quality=quality,
    )

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
        logger.warning(
            "Attempting cleanup of potentially incomplete frames in %s", output_dir
        )
        for i in range(start_frame, end_frame):
            potential_file = output_dir / f"frame_{i:07d}.{ext}"
            if potential_file.exists():
                try:
                    safe_unlink_file(potential_file, missing_ok=True)
                except OSError as unlink_err:
                    logger.error(
                        "Failed to delete potential frame %s during cleanup: %s",
                        potential_file,
                        unlink_err,
                    )
        raise RuntimeError(
            f"FFmpeg frame range extraction failed for {video_path}"
        ) from e
    except Exception as e:
        logger.error(
            "An unexpected error occurred during FFmpeg execution: %s", e, exc_info=True
        )
        raise RuntimeError(
            f"Unexpected error during FFmpeg frame range extraction for {video_path}"
        ) from e

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
            logger.warning(
                "Expected frame file %s not found after extraction.", frame_file
            )

    logger.info(
        "Found %d extracted frame files in range [%d, %d) for video %s.",
        len(extracted_files),
        start_frame,
        end_frame,
        video_path.name,
    )
    return extracted_files
