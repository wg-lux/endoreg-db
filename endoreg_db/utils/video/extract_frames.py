import os
import shutil
from pathlib import Path
from icecream import ic
import subprocess
from django.db import transaction
from tqdm import tqdm
from typing import TYPE_CHECKING, Union, List

if TYPE_CHECKING:
    from endoreg_db.models import RawVideoFile, Video

from django.core.files import File
import io


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


def extract_frames(
    video: Union["RawVideoFile", "Video"],
    quality: int = 2,
    overwrite: bool = False,
    ext="jpg",
    verbose=False,
) -> List[Path]:
    """
    Extract frames from the video file and save them to the frame_dir.
    For this, ffmpeg must be available in in the current environment.
    """
    frame_dir = Path(video.frame_dir)
    ic(f"Extracting frames to {frame_dir}")
    if not frame_dir.exists():
        frame_dir.mkdir(parents=True, exist_ok=True)

    if not overwrite and len(list(frame_dir.glob(f"*.{ext}"))) > 0:
        video.state_frames_extracted = True  # Mark frames as extracted
        extracted_paths = sorted(frame_dir.glob(f"*.{ext}"))
        return extracted_paths

    video_path = Path(video.file.path).resolve().as_posix()

    frame_path_string = frame_dir.resolve().as_posix()
    command = [
        "ffmpeg",
        "-i",
        video_path,  #
        "-q:v",
        str(quality),
        os.path.join(frame_path_string, f"frame_%07d.{ext}"),
    ]

    # Ensure FFmpeg is available
    if not shutil.which("ffmpeg"):
        raise EnvironmentError(
            "FFmpeg could not be found. Ensure it is installed and in your PATH."
        )

    # Extract frames from the video file
    # Execute the command
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    stdout_data, stderr_data = process.communicate()

    if process.returncode != 0:
        raise Exception(f"Error extracting frames: {stderr_data}")

    if verbose and stdout_data:
        print(stdout_data)

    # After extracting frames with ffmpeg, parse frame filenames and batch-create
    extracted_paths = sorted(frame_dir.glob(f"*.{ext}"))

    return extracted_paths


def initialize_frame_objects(
    video: Union["RawVideoFile", "Video"], extracted_paths: List[Path]
):
    """
    Initialize frame objects for the extracted frames.
    """
    if video.state_frames_initialized:
        return
    video.frame_count = len(extracted_paths)
    frames_to_create = []
    batch_size = int(os.environ.get("DJANGO_FFMPEG_EXTRACT_FRAME_BATCHSIZE", "500"))
    for i, (frame_number, file_obj) in tqdm(
        enumerate(prepare_bulk_frames(extracted_paths), start=1)
    ):
        frame_obj_instance = video.create_frame_object(
            frame_number, image_file=file_obj, extracted=True
        )
        frames_to_create.append(frame_obj_instance)

        if i % batch_size == 0:
            with transaction.atomic():
                video.bulk_create_frames(frames_to_create)
            frames_to_create.clear()

    if frames_to_create:
        with transaction.atomic():
            video.bulk_create_frames(frames_to_create)

    video.set_frames_extracted(True)
    video.save()

    frame_dir = extracted_paths[0].parent
    ic(f"Removing frame directory: {frame_dir}")
    shutil.rmtree(frame_dir)
