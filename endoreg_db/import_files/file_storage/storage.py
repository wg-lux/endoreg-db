import logging
from pathlib import Path

from endoreg_db.models.media.video.create_from_file import atomic_copy_with_fallback
from endoreg_db.utils.file_operations import ensure_directory
from endoreg_db.utils.video.ffmpeg_wrapper import transcode_video
from endoreg_db.import_files.context.import_context import ImportContext

logger = logging.getLogger(__name__)


def ensure_dir(path: Path) -> None:
    ensure_directory(path)


def create_sensitive_copy(src: Path, sensitive_root: Path, ctx: ImportContext) -> Path:
    """
    Create a sensitive copy of `src` in `sensitive_root`.

    Returns:
        Path to the sensitive copy.
    """
    ensure_dir(sensitive_root)
    dest = sensitive_root / src.name
    logger.info("Creating sensitive copy: %s -> %s", src, dest)
    if ctx.file_type == "video":
        transcode_video(src, dest)
        return dest
    atomic_copy_with_fallback(src, dest)
    return dest
