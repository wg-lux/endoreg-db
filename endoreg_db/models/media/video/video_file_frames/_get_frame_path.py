# --- Frame Creation/Deletion ---
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile

logger = logging.getLogger(__name__)


def _get_frame_path(video: "VideoFile", frame_number: int) -> Optional[Path]:
    """Constructs the expected path for a given frame number."""
    target_dir = video.get_frame_dir_path()  # Use IO helper
    if not target_dir:
        logger.warning("Cannot get frame path for video %s: Frame directory not set.", video.uuid)
        return None

    frame_filename = f"frame_{frame_number:07d}.jpg"
    path = target_dir / frame_filename
    return path
