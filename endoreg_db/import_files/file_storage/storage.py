import logging
from pathlib import Path
from endoreg_db.models.media.video.create_from_file import (
    atomic_copy_with_fallback,
)

logger = logging.getLogger(__name__)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def create_sensitive_copy(src: Path, sensitive_root: Path) -> Path:
    """
    Create a sensitive copy of `src` in `sensitive_root`.

    Returns:
        Path to the sensitive copy.
    """
    ensure_dir(sensitive_root)
    dest = sensitive_root / src.name
    logger.info("Creating sensitive copy: %s -> %s", src, dest)
    atomic_copy_with_fallback(src, dest)
    return dest
