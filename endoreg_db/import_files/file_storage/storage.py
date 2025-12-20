import logging
from pathlib import Path
from endoreg_db.models.media.video.create_from_file import (
    atomic_copy_with_fallback,
    atomic_move_with_fallback,
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


def move_to_anonymized(temp_path: Path, anonymized_root: Path) -> Path:
    """
    Move a (temporary) anonymized file into the canonical anonymized root.

    Returns:
        Final path inside anonymized_root.
    """
    ensure_dir(anonymized_root)
    dest = anonymized_root / temp_path.name
    logger.info("Moving anonymized file: %s -> %s", temp_path, dest)
    atomic_move_with_fallback(temp_path, dest)
    return dest
