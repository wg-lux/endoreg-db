

from typing import TYPE_CHECKING

import logging

if TYPE_CHECKING:
    from endoreg_db.models import VideoFile, Frame
    
logger = logging.getLogger(__name__)

def _create_frame_object(
    video: "VideoFile", frame_number: int, relative_path: str, extracted: bool = False
) -> "Frame":
    """Instantiates a Frame object (does not save it)."""
    from endoreg_db.models import Frame

    return Frame(
        video=video,
        frame_number=frame_number,
        relative_path=relative_path,
        is_extracted=extracted,
    )
