# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import transaction


# Assuming ffmpeg_wrapper has or will have this function
from endoreg_db.utils.file_operations import (
    safe_unlink_file,
)

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)


def _delete_frame_range(video: "VideoFile", start_frame: int, end_frame: int) -> None:
    """Delete frame files in [start_frame, end_frame) and clear DB flags after deletion."""

    logger.info(
        "Deleting frame files for video %s in range [%d, %d)",
        video.raw_video_hash,
        start_frame,
        end_frame,
    )
    frames_to_delete = video.frames.filter(
        frame_number__gte=start_frame,
        frame_number__lt=end_frame,
    )

    deleted_count = 0
    paths_to_delete: list[Path] = [
        frame.file_path for frame in frames_to_delete
    ]  # Get paths before potential DB changes

    failed_paths: list[Path] = []
    for frame_path in paths_to_delete:
        try:
            if frame_path.exists():
                safe_unlink_file(frame_path)
                deleted_count += 1
        except Exception as e:
            logger.warning(
                "Could not delete frame file %s for video %s: %s",
                frame_path,
                video.raw_video_hash,
                e,
            )
            failed_paths.append(frame_path)

    if failed_paths:
        raise RuntimeError(
            "Could not delete all frame files for "
            f"video {video.raw_video_hash} range [{start_frame}, {end_frame}). "
            f"failed_count={len(failed_paths)}"
        )

    with transaction.atomic():
        update_count = frames_to_delete.update(is_extracted=False)
    logger.info(
        "Marked %d Frame objects as is_extracted=False for video %s range [%d, %d).",
        update_count,
        video.raw_video_hash,
        start_frame,
        end_frame,
    )

    logger.info(
        "Attempted deletion of %d files for video %s range [%d, %d). Actual deleted: %d.",
        len(paths_to_delete),
        video.raw_video_hash,
        start_frame,
        end_frame,
        deleted_count,
    )


def _extract_frame_range(
    video: "VideoFile",
    start_frame: int,
    end_frame: int,
    quality: int = 2,
    overwrite: bool = False,
    ext: str = "jpg",
    verbose: bool = False,
) -> bool:
    raise RuntimeError(
        "Frame file materialization is export-only; use video frame streaming."
    )
