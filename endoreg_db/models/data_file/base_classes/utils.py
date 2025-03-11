"""
Utility functions for data file classes.
"""

import os
from pathlib import Path
import cv2
import numpy as np

DJANGO_NAME_SALT = os.environ.get("DJANGO_NAME_SALT", "default_salt")

# Directory stuff
PSEUDO_DIR = Path(os.environ.get("DJANGO_PSEUDO_DIR", Path("./erc_data")))
STORAGE_LOCATION = PSEUDO_DIR
FRAME_DIR_NAME = os.environ.get("DJANGO_FRAME_DIR_NAME", "db_frames")
RAW_FRAME_DIR_NAME = os.environ.get("DJANGO_RAW_FRAME_DIR_NAME", "db_raw_frames")
VIDEO_DIR_NAME = os.environ.get("DJANGO_VIDEO_DIR_NAME", "db_videos")
RAW_VIDEO_DIR_NAME = os.environ.get("DJANGO_RAW_VIDEO_DIR_NAME", "db_raw_videos")

FRAME_DIR = STORAGE_LOCATION / FRAME_DIR_NAME
VIDEO_DIR = STORAGE_LOCATION / VIDEO_DIR_NAME
RAW_VIDEO_DIR = STORAGE_LOCATION / RAW_VIDEO_DIR_NAME

VIDEO_DIR.mkdir(parents=True, exist_ok=True)
RAW_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# AI Stuff
FRAME_PROCESSING_BATCH_SIZE = os.environ.get("DJANGO_FRAME_PROCESSING_BATCH_SIZE", 10)


def get_transcoded_file_path(source_file_path: Path, suffix: str = "mp4"):
    """
    Method to get the transcoded file path.

    Args:
        source_file_path (Path): Source file path.
        suffix (str): Suffix of the transcoded file.

    Returns:
        transcoded_file_path (Path): Transcoded file path.
    """
    transcoded_file_name = f"{source_file_path.stem}_transcoded.{suffix}"
    transcoded_file_path = source_file_path.parent / transcoded_file_name
    return transcoded_file_path


def anonymize_frame(raw_frame_path: Path, target_frame_path: Path, endo_roi):
    """
    Anonymize the frame by blacking out all pixels that are not in the endoscope ROI.
    """

    frame = cv2.imread(raw_frame_path.as_posix())  # pylint: disable=no-member

    # make black frame with same size as original frame
    new_frame = np.zeros_like(frame)

    # endo_roi is dict with keys "x", "y", "width", "heigth"
    x = endo_roi["x"]
    y = endo_roi["y"]
    width = endo_roi["width"]
    height = endo_roi["height"]

    # copy endoscope roi to black frame
    new_frame[y : y + height, x : x + width] = frame[y : y + height, x : x + width]
    cv2.imwrite(target_frame_path.as_posix(), new_frame)  # pylint: disable=no-member

    return frame


def copy_with_progress(src: str, dst: str, buffer_size=1024 * 1024):
    """
    Make a copy of a file with progress bar.

    Args:
        src (str): Source file path.
        dst (str): Destination file path.
        buffer_size (int): Buffer size for copying.
    """
    total_size = os.path.getsize(src)
    copied_size = 0

    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            buf = fsrc.read(buffer_size)
            if not buf:
                break
            fdst.write(buf)
            copied_size += len(buf)
            progress = copied_size / total_size * 100
            print(f"\rProgress: {progress:.2f}%", end="")
