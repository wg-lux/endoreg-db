import os
import shutil
from pathlib import Path
from icecream import ic
import subprocess
from django.db import transaction
from tqdm import tqdm
from typing import TYPE_CHECKING, Union, List, Optional

if TYPE_CHECKING:
    from ...models.media import VideoFile

from django.core.files import File
import io
from .ffmpeg_wrapper import extract_frames as ffmpeg_extract_frames


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


def extract_frames(video_path: Path, output_dir: Path, quality: int, ext: str = "jpg", fps: Optional[float] = None) -> List[Path]:
    """Extracts frames from a video file using ffmpeg_wrapper."""
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    return ffmpeg_extract_frames(video_path, output_dir, quality, ext, fps)


def initialize_frame_objects(
    video: "VideoFile", extracted_paths: List[Path]
):
    """
    Initialize frame objects for the extracted frames and update state.
    """
    state = video.get_or_create_state()
    # Check state before proceeding
    if state.frames_initialized:
        ic(f"Frames already initialized for video {video.uuid}, skipping.")
        return

    if not extracted_paths:
        ic(f"No extracted paths provided for video {video.uuid}, cannot initialize frames.")
        return

    video.frame_count = len(extracted_paths)
    frames_to_create = []
    batch_size = int(os.environ.get("DJANGO_FFMPEG_EXTRACT_FRAME_BATCHSIZE", "500"))

    # Prepare frame data (relative paths for storage)
    frame_dir = video.get_frame_dir_path()
    if not frame_dir:
         raise ValueError(f"Frame directory not set for video {video.uuid}")

    storage_base_path = Path(video._meta.get_field('raw_file').storage.location) # Get storage root

    for i, path in tqdm(enumerate(extracted_paths, start=1)):
        frame_number = int(path.stem.split("_")[1]) - 1 # Assuming frame_0000001.jpg is frame_number 0
        relative_path = path.relative_to(storage_base_path).as_posix() # Path relative to MEDIA_ROOT

        # Create Frame instance (without saving yet)
        frame_obj_instance = video.create_frame_object(
            frame_number, image_file=relative_path, extracted=True
        )
        frames_to_create.append(frame_obj_instance)

        if i % batch_size == 0:
            with transaction.atomic():
                video.bulk_create_frames(frames_to_create)
            frames_to_create.clear()

    if frames_to_create:
        with transaction.atomic():
            video.bulk_create_frames(frames_to_create)

    # Update state and save VideoFile (to save frame_count)
    state.frames_initialized = True
    state.save(update_fields=['frames_initialized'])
    video.save(update_fields=['frame_count']) # Save frame_count on VideoFile

