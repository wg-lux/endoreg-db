import logging
from pathlib import Path
from uuid import uuid4

from endoreg_db.import_files.context.import_context import ImportContext
from endoreg_db.services.video_files._imports import atomic_copy_with_fallback
from endoreg_db.utils.ffmpeg_wrapper import transcode_videofile_if_required
from endoreg_db.utils.file_operations import ensure_directory

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
    hash_prefix = str(getattr(ctx, "file_hash", None) or "unhashed")[:16]
    staging_dir = ensure_directory(sensitive_root / f"{hash_prefix}-{uuid4().hex}")
    dest = staging_dir / src.name
    logger.info("Creating sensitive copy: %s -> %s", src, dest)
    if ctx.file_type == "video":
        transcoded_path = transcode_videofile_if_required(src, dest)
        if transcoded_path is None:
            raise RuntimeError(
                "Video transcode failed; refusing to continue with missing sensitive copy "
                f"for {src}."
            )
        if not transcoded_path.exists() or transcoded_path.stat().st_size <= 0:
            raise RuntimeError(
                "Video transcode did not produce a usable sensitive copy "
                f"at {transcoded_path}."
            )
        return transcoded_path
    atomic_copy_with_fallback(src, dest)
    return dest
