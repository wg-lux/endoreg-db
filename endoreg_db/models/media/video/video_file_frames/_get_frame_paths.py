import logging
from pathlib import Path
from typing import TYPE_CHECKING, List

from endoreg_db.models.media.video.video_file_io import _get_frame_dir_path

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def _get_frame_paths(video: "VideoFile") -> List[Path]:
    """Returns a sorted list of Path objects for extracted frame image files."""
    frame_dir = _get_frame_dir_path(video)
    if not frame_dir or not frame_dir.exists():
        logger.warning("Frame directory %s does not exist for video %s.", frame_dir, video.uuid)
        return []

    frame_paths = list(frame_dir.glob("frame_*.jpg"))

    try:
        frame_paths.sort(key=lambda p: int(p.stem.split("_")[-1]))
    except (ValueError, IndexError) as e:
        logger.error("Error sorting frame paths in %s: %s. Found paths: %s", frame_dir, e, [p.name for p in frame_paths], exc_info=True)
        logger.warning("Falling back to unsorted frame paths to preserve available data.")
        return frame_paths
    return frame_paths
