from __future__ import annotations

import subprocess
from pathlib import Path

from endoreg_db.utils.file_operations import ensure_directory, safe_rmtree


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
    """
    Extract specific frames from a video using the same quality logic as the original extractor.
    """
    if output_dir.exists():
        safe_rmtree(output_dir)
    ensure_directory(output_dir)

    for frame_number in frame_numbers:
        timestamp_sec = frame_number / fps
        output_file = output_dir / f"frame_{str(frame_number).zfill(7)}.{ext}"
        extract_single_frame(
            input_path=str(video_path),
            timestamp=timestamp_sec,
            output_path=str(output_file),
            quality=quality,
            ext=ext,
        )
