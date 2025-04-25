#!/usr/bin/env python
import argparse
from endoreg_db.utils.video import split_video
from pathlib import Path


# Example Usage (optional - can be removed or placed under if __name__ == '__main__':)
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Split a video file into segments of a specified interval.")
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Path to the input video file."
    )
    parser.add_argument(
        "-t", "--interval",
        type=int,
        default=10,
        help="Interval duration for each segment in seconds (default: 10)."
    )

    args = parser.parse_args()

    input_video_path = Path(args.input)

    # The check for file existence is already handled inside split_video
    # but we can add an extra check here for a clearer error message earlier.
    if not input_video_path.is_file():
        print(f"Error: Input video file not found at {args.input}")
    else:
        split_video(str(input_video_path), args.interval)

