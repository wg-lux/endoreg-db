from ..utils import (
    random_day_by_month_year,
    get_uuid_filename,
    random_day_by_year,
    get_examiner_hash,
    ensure_aware_datetime,
    get_hash_string,
    get_pdf_hash,
    get_video_hash,
    create_mock_examiner_name,
    create_mock_patient_name,
    data_paths,
    get_patient_examination_hash,
    DJANGO_NAME_SALT,
    guess_name_gender,
)
from django.core.files import File
from django.core.files.storage import FileSystemStorage
import io
import os
from icecream import ic
from tqdm import tqdm
import numpy as np
import cv2
from typing import TYPE_CHECKING, Dict, List
from pathlib import Path
from django.db import models
if TYPE_CHECKING:
    from ..models.media import VideoFile, Frame

from logging import getLogger

logger = getLogger(__name__)

STORAGE_DIR = data_paths["storage"]
FILE_STORAGE = FileSystemStorage(location = STORAGE_DIR)
VIDEO_DIR = data_paths["video"]
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
    raw_frame_path: Path, target_frame_path: Path, endo_roi, all_black: bool = False
):
    """
    Anonymize the frame by blacking out all pixels that are not in the endoscope ROI.
    """

    frame = cv2.imread(raw_frame_path.as_posix())  # pylint: disable=no-member
    if frame is None:
        raise FileNotFoundError(f"Could not read frame at {raw_frame_path}")

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


def _create_anonymized_frame_files(
    video: "VideoFile",
    anonymized_frame_dir: Path,
    endo_roi: Dict[str, int],
    frames: List["Frame"],
    outside_frame_numbers: set,
) -> List[Path]:

    generated_frame_paths = []

    # Ensure frames is iterable (handle QuerySet or list)
    frame_iterator = frames.iterator() if isinstance(frames, models.QuerySet) else iter(frames)

    # anonymize frames: copy endo-roi content while making other pixels black.
    for frame in tqdm(frame_iterator, total=frames.count() if isinstance(frames, models.QuerySet) else len(frames), desc=f"Anonymizing frames for {video.uuid}"):
        try:
            frame_path = Path(frame.image.path)
            frame_name = frame_path.name
            frame_number = frame.frame_number

            # Check if frame number is in the set of outside frames
            all_black = frame_number in outside_frame_numbers

            target_frame_path = anonymized_frame_dir / frame_name
            anonymize_frame(
                frame_path, target_frame_path, endo_roi, all_black=all_black
            )
            generated_frame_paths.append(target_frame_path)
        except Exception as e:
            logger.error("Error processing frame %s (Number: %d) for anonymization: %s", frame.pk, getattr(frame, 'frame_number', -1), e, exc_info=True)
            
    return generated_frame_paths


def _censor_outside_frames(
    video: "VideoFile",
):
    outside_frame_paths = video.get_outside_frame_paths()

    if not outside_frame_paths:
        logger.info("No outside frames found to censor for video %s", video.uuid)

    else:
        logger.info("Censoring %d outside frames for video %s", len(outside_frame_paths), video.uuid)

        censored_count = 0
        error_count = 0
        for frame_path in tqdm(iterable=outside_frame_paths, desc=f"Censoring frames for {video.uuid}"):
            try:
                frame = cv2.imread(frame_path.as_posix())
                if frame is None:
                    logger.warning("Could not read frame for censoring: %s", frame_path)
                    error_count += 1
                    continue
                frame.fill(0)
                if not cv2.imwrite(filename=frame_path.as_posix(), img=frame):
                    logger.error("Failed to write censored frame: %s", frame_path)
                    error_count += 1
                else:
                    censored_count += 1
            except Exception as e:
                logger.error("Error censoring frame %s: %s", frame_path, e, exc_info=True)
                error_count += 1

        logger.info("Finished censoring for video %s. Censored: %d, Errors: %d", video.uuid, censored_count, error_count)
        if error_count > 0:
            return False

    return True

def _get_anonymized_frame_dir(video: "VideoFile") -> Path:
    """
    Get the path to the temporary anonymized frame directory.
    Uses the VideoFile's UUID.
    """
    return video._get_temp_anonymized_frame_dir()


def _get_anonymized_video_path(video: "VideoFile") -> Path:
    """
    Get the final path for the anonymized video file.
    Uses the VideoFile's UUID and raw file name structure.
    """
    return video._get_target_anonymized_video_path()


__all__ = [
    "_censor_outside_frames",
    "_get_anonymized_frame_dir",
    "_get_anonymized_video_path",
    "random_day_by_month_year",
    "random_day_by_year",
    "get_examiner_hash",
    "ensure_aware_datetime",
    "get_hash_string",
    "get_pdf_hash",
    "get_video_hash",
    "create_mock_examiner_name",
    "create_mock_patient_name",
    "data_paths",
    "get_patient_examination_hash",
    "DJANGO_NAME_SALT",
    "guess_name_gender",
    "FILE_STORAGE",
    "VIDEO_DIR",
    "ANONYM_VIDEO_DIR",
    "FRAME_DIR",
    "WEIGHTS_DIR",
    "prepare_bulk_frames",
    "_create_anonymized_frame_files",
    "get_uuid_filename",
]