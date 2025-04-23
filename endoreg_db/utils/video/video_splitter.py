import os
import subprocess
import pathlib
import math
import logging
import json

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_video_duration(video_path: pathlib.Path) -> float:
    """Gets the duration of a video file using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())
        return duration
    except subprocess.CalledProcessError as e:
        logging.error(f"Error getting duration for {video_path}: {e.stderr}")
        raise
    except ValueError:
        logging.error(f"Could not parse duration from ffprobe output: {result.stdout}")
        raise

def split_video(input_path: str, interval: int):
    """
    Splits a video into segments of a specified interval using ffmpeg.

    Args:
        input_path: Path to the input MP4 video file.
        interval: The desired duration of each segment in seconds.
    """
    input_file = pathlib.Path(input_path)
    if not input_file.is_file():
        logging.error(f"Input file not found: {input_path}")
        return

    video_name = input_file.stem
    output_dir = pathlib.Path("data/video_splitter") / video_name
    output_dir.mkdir(parents=True, exist_ok=True)
    logging.info(f"Created output directory: {output_dir}")

    try:
        duration = get_video_duration(input_file)
        logging.info(f"Video duration: {duration:.2f} seconds")
    except Exception as e:
        logging.error(f"Could not get video duration. Aborting split. Error: {e}")
        return

    num_segments = math.ceil(duration / interval)
    logging.info(f"Splitting into {num_segments} segments of approximately {interval} seconds each.")

    for i in range(num_segments):
        start_time = i * interval
        output_filename = output_dir / f"segment_{i+1:03d}{input_file.suffix}"

        # Use -t for interval duration. For the last segment, ffmpeg with -c copy
        # might automatically stop at the end, or we could calculate exact duration.
        # Using -t interval is simpler and usually works well with -c copy.
        # If the last segment needs precise duration without relying on -c copy behavior:
        # segment_duration = min(interval, duration - start_time)
        # However, -t interval with -c copy is generally robust.

        cmd = [
            "ffmpeg",
            "-i", str(input_file),
            "-ss", str(start_time),
            "-t", str(interval),
            "-c", "copy",  # Fast, lossless splitting
            "-avoid_negative_ts", "make_zero", # Avoids issues with negative timestamps
            str(output_filename)
        ]

        logging.info(f"Running command: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logging.info(f"Successfully created segment: {output_filename}")
            if result.stderr: # ffmpeg often outputs info to stderr
                 logging.debug(f"ffmpeg output for segment {i+1}:\n{result.stderr}")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error creating segment {i+1}: {output_filename}")
            logging.error(f"Command failed: {' '.join(cmd)}")
            logging.error(f"ffmpeg stderr:\n{e.stderr}")
            # Decide if you want to stop on error or continue
            # return # Uncomment to stop on first error

    logging.info("Video splitting completed.")

