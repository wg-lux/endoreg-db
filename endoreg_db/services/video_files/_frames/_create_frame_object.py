import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.frame.frame import Frame
    from endoreg_db.models.media.video.video_file import VideoFile

logger = logging.getLogger(__name__)

__all__ = ["_create_frame_object"]


def _create_frame_object(
    video: "VideoFile", frame_number: int, relative_path: str, extracted: bool = False
) -> "Frame":
    """Instantiates a Frame object (does not save it)."""
    from endoreg_db.models.media.frame.frame import Frame

    return Frame(
        video=video,
        frame_number=frame_number,
        relative_path=relative_path,
        is_extracted=extracted,
    )
