import logging
from pathlib import Path
from typing import Iterable
from endoreg_db.utils.paths import (
    ANONYM_REPORT_DIR,
    ANONYM_VIDEO_DIR,
    IMPORT_REPORT_DIR,
    IMPORT_VIDEO_DIR,
    SENSITIVE_REPORT_DIR,
    SENSITIVE_VIDEO_DIR,
)

dirs = [
    ANONYM_REPORT_DIR,
    ANONYM_VIDEO_DIR,
    IMPORT_REPORT_DIR,
    IMPORT_VIDEO_DIR,
    SENSITIVE_REPORT_DIR,
    SENSITIVE_VIDEO_DIR,
]


logger = logging.getLogger(__name__)


def validate_directories(dirs: Iterable[Path] = dirs) -> bool:
    """
    Ensure all directories in `dirs` exist.
    Missing directories are created automatically.

    Args:
        dirs: Iterable of Path objects representing directories.

    Returns:
        bool: True if all directories exist or were created successfully,
              False if any directory could not be created.
    """
    ok = True

    for d in dirs:
        try:
            if not d.exists():
                logger.info(f"Directory missing, creating: {d}")
                d.mkdir(parents=True, exist_ok=True)

            if not d.is_dir():
                logger.error(f"Path exists but is not a directory: {d}")
                ok = False

        except Exception as e:
            logger.error(f"Failed to create or validate directory '{d}': {e}")
            ok = False

    return ok


validate_directories(dirs)
