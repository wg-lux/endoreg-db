from __future__ import annotations

import subprocess
from pathlib import Path

from endoreg_db.utils.file_operations import ensure_directory, safe_rmtree
from endoreg_db.utils.ffmpeg_wrapper import extract_frame_range


def extract_single_frame(
    input_path: str,
    timestamp: float,
    output_path: str,
    quality: int = 2,
    ext: str = "png",
) -> None:
    """
    Extract a single frame from a video using ffmpeg.
    """
    cmd: list[str] = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        input_path,
        "-frames:v",
        "1",
        "-an",
        "-q:v",
        str(quality),
        output_path,
    ]
    subprocess.run(cmd, check=True)


def extract_selected_frames(
    video_path: Path,
    frame_numbers: list[int],
    output_dir: Path,
    fps: int = 50,
    quality: int = 2,
    ext: str = "png",
) -> None:
    """Extract source frame identities; ``fps`` remains a legacy API argument."""
    _ = fps
    requested = sorted(set(frame_numbers))
    if any(frame_number < 0 for frame_number in requested):
        raise ValueError("frame_numbers must be non-negative")
    if output_dir.exists():
        safe_rmtree(output_dir)
    ensure_directory(output_dir)

    for frame_number in requested:
        extracted = extract_frame_range(
            video_path,
            output_dir,
            start_frame=frame_number,
            end_frame=frame_number + 1,
            quality=quality,
            ext=ext,
        )
        if len(extracted) != 1:
            raise RuntimeError(
                f"Could not extract source frame {frame_number} from {video_path}"
            )
