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
    """Extracts frames from a video file."""
    return ffmpeg_extract_frames(video_path, output_dir, quality, ext, fps)


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
