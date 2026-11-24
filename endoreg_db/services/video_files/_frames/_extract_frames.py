# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportMissingTypeStubs=false
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from endoreg_db.models.media.video.video_file import VideoFile


def _extract_frames(
    video: "VideoFile",
    quality: int = 2,
    overwrite: bool = False,
    ext: str = "jpg",
    verbose: bool = False,
    from_processed: bool = False,
) -> bool:
    raise RuntimeError(
        "Frame file materialization is export-only; use video frame streaming."
    )
