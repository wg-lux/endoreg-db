from pathlib import Path
import shutil

def extract_selected_frames(
    video_path: Path,
    frame_numbers: list,
    output_dir: Path,
    fps: int = 50,
    quality: int = 2,
    ext: str = "png"
):
    """
    Extract specific frames from a video using the same quality logic as the original extractor.

    This will:
    - Convert frame numbers to timestamps using the provided FPS.
    - Overwrite any existing frames for a clean run.
    - Save frames with consistent formatting (zero-padded).

    Args:
        video_path (Path): Path to the input video.
        frame_numbers (list): List of frame numbers to extract.
        output_dir (Path): Where to save the frames.
        fps (int): Frames per second of the video.
        quality (int): Quality setting (2 is default).
        ext (str): File extension for output (e.g., 'png/jpg').
    """
    # Optional: Clean up old frames before extraction
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for frame_number in frame_numbers:
        # Convert frame number to timestamp in seconds
        timestamp_sec = frame_number / fps

        # Build output filename
        output_file = output_dir / f"frame_{str(frame_number).zfill(7)}.{ext}"

        # Extract and save the frame
        extract_single_frame(
            input_path=str(video_path),
            timestamp=timestamp_sec,
            output_path=str(output_file),
            quality=quality,
            ext=ext
        )
import subprocess
from pathlib import Path

def extract_single_frame(input_path: str, timestamp: float, output_path: str, quality: int = 2, ext: str = "png"):
    """
    Extract a single frame from a video using ffmpeg.

    Args:
        input_path (str): Path to the video file.
        timestamp (float): Time in seconds to extract the frame.
        output_path (str): Where to save the extracted frame.
        quality (int): pnj quality (2 = high, 31 = low).
        ext (str): File extension (e.g., 'jpg').
    """
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-ss", f"{timestamp:.3f}",
        "-i", str(input_path),
        "-frames:v", "1",
        "-q:v", str(quality),
        str(output_path)
    ]

    subprocess.run(cmd, check=True)
