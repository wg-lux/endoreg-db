from ..utils import (
    random_day_by_month_year,
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

from icecream import ic
import subprocess
from tqdm import tqdm
import numpy as np
import cv2
from typing import TYPE_CHECKING, Dict, List
from pathlib import Path
if TYPE_CHECKING:
    from endoreg_db.models import RawVideoFile, Frame

STORAGE_DIR = data_paths["storage"]
ANONYM_VIDEO_DIR = data_paths["video_export"]
WEIGHTS_DIR = data_paths["weights"]
STORAGE_DIR = data_paths["storage"]
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


def _create_anonymized_frame_files(
    anonymized_frame_dir:Path,
    endo_roi:Dict[str, int], 
    frames :List["Frame"],
    outside_frame_numbers:List[int],
    ) -> List[Path]:

    generated_frame_paths = []
    
    # anonymize frames: copy endo-roi content while making other pixels black. (frames are Path objects to jpgs or pngs)
    for frame in tqdm(frames):
        frame_path = Path(frame.image.path)
        frame_name = frame_path.name
        frame_number = frame.frame_number

        if frame_number in outside_frame_numbers:
            all_black = True
        else:
            all_black = False

        target_frame_path = anonymized_frame_dir / frame_name
        anonymize_frame(
            frame_path, target_frame_path, endo_roi, all_black=all_black
        )
        generated_frame_paths.append(target_frame_path)

    return generated_frame_paths

def _assemble_anonymized_video(
    generated_frame_paths: List[Path],
    anonymized_video_path: Path,
    fps: int,
) -> Path:
    # Use ffmpeg and the frame paths to create a video

    height, width = cv2.imread(filename=generated_frame_paths[0].as_posix()).shape[:2]
    ic("Assembling anonymized video")
    ic(f"Frame width: {width}, height: {height}")
    ic(f"FPS: {fps}")

    command = [
        "ffmpeg",
        "-y",
        "-pattern_type",
        "glob",
        "-f",
        "image2",
        "-framerate",
        str(fps),
        "-i",
        f"{generated_frame_paths[0].parent.as_posix()}/frame_[0-9]*.jpg",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        f"scale={width}:{height}",
        anonymized_video_path.as_posix(),
    ]

    subprocess.run(command, check=True)
    ic(f"Anonymized video saved at {anonymized_video_path}")
    return anonymized_video_path

def _censor_outside_frames(
    raw_video: "RawVideoFile",
):
    raw_video.save()
    outside_frame_paths = raw_video.get_outside_frame_paths()

    if not outside_frame_paths:
        ic("No outside frames found")

    else:
        ic(f"Found {len(outside_frame_paths)} outside frames")
        # use cv2 to replace all outside frames with completely black frames

        for frame_path in tqdm(iterable=outside_frame_paths):
            frame = cv2.imread(frame_path.as_posix())
            if frame is None:
                continue  # or raise, depending on policy
            frame.fill(0)
            cv2.imwrite(filename=frame_path.as_posix(), img=frame)

    return True

def _get_anonymized_frame_dir(raw_video: "RawVideoFile") -> Path:
    """
    Get the path to the anonymized frame directory.
    """
    anonymized_frame_dir = Path(raw_video.frame_dir).parent / f"tmp_{raw_video.uuid}"
    return anonymized_frame_dir

def _get_anonymized_video_path(raw_video:"RawVideoFile") -> Path:
    """
    Get the path to the anonymized video file.
    """
    video_dir = ANONYM_VIDEO_DIR

    video_dir.mkdir(parents=True, exist_ok=True)
    video_suffix = Path(raw_video.file.path).suffix
    video_name = f"{raw_video.uuid}{video_suffix}"
    anonymized_video_name = f"anonymized_{video_name}"
    anonymized_video_path = video_dir / anonymized_video_name

    return anonymized_video_path

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
]