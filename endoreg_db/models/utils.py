from ..utils import (
    data_paths,
    DJANGO_NAME_SALT,
)
from django.core.files import File
from django.core.files.storage import FileSystemStorage
import io
import os
import numpy as np
import cv2
from typing import TYPE_CHECKING, List, Tuple
from pathlib import Path
if TYPE_CHECKING:
    pass

from logging import getLogger

logger = getLogger(__name__)

STORAGE_DIR = data_paths["storage"]
FILE_STORAGE = FileSystemStorage(location = STORAGE_DIR)
VIDEO_DIR = data_paths["video"]
TMP_VIDEO_DIR = VIDEO_DIR / "tmp"
ANONYM_VIDEO_DIR = data_paths["video_export"]
FRAME_DIR = data_paths["frame"]
WEIGHTS_DIR = data_paths["weights"]
PDF_DIR = data_paths["raw_pdf"]
DOCUMENT_DIR = data_paths["pdf"]

TEST_RUN = os.environ.get("TEST_RUN", "False")
TEST_RUN = TEST_RUN.lower() == "true"

TEST_RUN_FRAME_NUMBER = int(os.environ.get("TEST_RUN_FRAME_NUMBER", "500"))


def prepare_bulk_frames(frame_paths: List[Path]):
    """
    Reads the frame paths into memory as Django File objects.
    This avoids 'seek of closed file' errors by using BytesIO for each frame.
    """
    for path in frame_paths:
        frame_number = int(path.stem.split("_")[1])
        with open(path, "rb") as f:
            content = f.read()
        file_obj = File(io.BytesIO(content), name=path.name)
        yield frame_number, file_obj


def find_segments_in_prediction_array(prediction_array: np.array, min_frame_len: int):
    """
    Expects a prediction array of shape (num_frames) and a minimum frame length.
    Returns a list of tuples (start_frame_number, end_frame_number) that represent the segments.
    """
    # Add False to the beginning and end to detect changes at the array boundaries
    padded_prediction = np.pad(
        prediction_array, (1, 1), "constant", constant_values=False
    )

    # Find the start points and end points of the segments
    diffs = np.diff(padded_prediction.astype(int))
    segment_starts = np.where(diffs == 1)[0]
    segment_ends = np.where(diffs == -1)[0]

    # Filter segments based on min_frame_len
    segments = [
        (start, end)
        for start, end in zip(segment_starts, segment_ends)
        if end - start >= min_frame_len
    ]

    return segments

def anonymize_frame(
    raw_frame_path: Path, target_frame_path: Path, endo_roi, all_black: bool = False, censor_color: Tuple[int, int, int] = (0, 0, 0) # Added censor_color param
):
    """
    Anonymize the frame by blacking out pixels outside the endoscope ROI or making the whole frame black.
    """
    frame = cv2.imread(raw_frame_path.as_posix())
    if frame is None:
        # Raise error instead of returning None/frame
        raise FileNotFoundError(f"Could not read frame at {raw_frame_path}")

    # make black frame with same size as original frame
    new_frame = np.zeros_like(frame)

    if not all_black:
        # Validate ROI dictionary keys
        required_keys = {"x", "y", "width", "height"}
        if not required_keys.issubset(endo_roi):
            raise ValueError(f"Invalid endo_roi dictionary provided: {endo_roi}. Missing keys.")

        x = endo_roi["x"]
        y = endo_roi["y"]
        width = endo_roi["width"]
        height = endo_roi["height"]

        # Add boundary checks for ROI coordinates
        h_orig, w_orig, _ = frame.shape
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(w_orig, x + width), min(h_orig, y + height)

        if x1 >= x2 or y1 >= y2:
            logger.warning(f"ROI [{x},{y},{width},{height}] is outside or invalid for frame dimensions {w_orig}x{h_orig}. Resulting frame might be all black.")
        else:
            # copy valid endoscope roi part to black frame
            new_frame[y1:y2, x1:x2] = frame[y1:y2, x1:x2]
    else:
        # If all_black, fill with censor_color (defaults to black)
        new_frame[:] = censor_color

    # Check if writing the anonymized frame was successful
    success = cv2.imwrite(target_frame_path.as_posix(), new_frame)
    if not success:
        raise IOError(f"Failed to write anonymized frame to {target_frame_path}")


__all__ = [
    "DJANGO_NAME_SALT",
    "data_paths",
    "FILE_STORAGE",
    "VIDEO_DIR",
    "TMP_VIDEO_DIR",
    "ANONYM_VIDEO_DIR",
    "FRAME_DIR",
    "WEIGHTS_DIR",
    "PDF_DIR",
    "DOCUMENT_DIR",
    "prepare_bulk_frames",
    "anonymize_frame",
    "find_segments_in_prediction_array",
    "TEST_RUN",
    "TEST_RUN_FRAME_NUMBER",
]