"""
Utility functions for data file classes.
"""

import os
from pathlib import Path
import cv2
import numpy as np



def anonymize_frame(
    raw_frame_path: Path, target_frame_path: Path, endo_roi, all_black: bool = False
):
    """
    Anonymize the frame by blacking out all pixels that are not in the endoscope ROI.
    """

    frame = cv2.imread(raw_frame_path.as_posix())  # pylint: disable=no-member

    # make black frame with same size as original frame
    new_frame = np.zeros_like(frame)

    if not all_black:
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
