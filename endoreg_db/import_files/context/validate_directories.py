import logging
from pathlib import Path
from typing import Iterable
from endoreg_db.utils.file_operations import ensure_directory
from endoreg_db.utils.paths import get_runtime_paths


logger = logging.getLogger(__name__)


def validate_directories(dirs: Iterable[Path] | None = None) -> bool:
    """
    Ensure all directories in `dirs` exist.
    Missing directories are created automatically.

    Args:
        dirs: Iterable of Path objects representing directories.

    Returns:
        bool: True if all directories exist or were created successfully,
              False if any directory could not be created.
    """
    if dirs is None:
        dirs = [
            get_runtime_paths().anonym_report,
            get_runtime_paths().anonym_video,
            get_runtime_paths().import_report,
            get_runtime_paths().import_video,
            get_runtime_paths().import_anonymized_report,
            get_runtime_paths().import_anonymized_video,
            get_runtime_paths().import_frame,
            get_runtime_paths().weights_import,
            get_runtime_paths().sensitive_report,
            get_runtime_paths().sensitive_video,
        ]

    ok = True

    for d in dirs:
        try:
            if not d.exists():
                logger.info(f"Directory missing, creating: {d}")
                ensure_directory(d)

            if not d.is_dir():
                logger.error(f"Path exists but is not a directory: {d}")
                ok = False

        except Exception as e:
            logger.error(f"Failed to create or validate directory '{d}': {e}")
            ok = False

    return ok
