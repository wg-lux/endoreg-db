"""
Centralizes path management for the application.

This module sets up all necessary directories for storage and import operations.
It provides a unified dictionary 'data_paths' for accessing all path objects.
"""

from logging import getLogger

logger = getLogger(__name__)

import os
from pathlib import Path
from typing import Dict, Union

# Alternative approach using env_path helper, deprecated since monorepo setup. Alright for single install, env is always preferred.
# from endoreg_db.config.env import env_path

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "internal_storage"))

storage_dir_env = os.getenv("STORAGE_DIR")
if storage_dir_env is None:
    raise RuntimeError("STORAGE_DIR environment variable is not set.")
storage_dir = Path(storage_dir_env)
STORAGE_DIR = storage_dir
if not STORAGE_DIR.exists():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


PREFIX_RAW = "raw_"
io_dir_env = Path(os.getenv("IO_DIR", "data"))
io_dir = Path(io_dir_env)
IO_DIR = io_dir
if not STORAGE_DIR.exists():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)


# Data dropoff folders - These can be external, determined by IO_DIR (Default: set to desktop root folder of OS)

IMPORT_DIR_NAME = "import"
EXPORT_DIR_NAME = "export"

IMPORT_DIR = IO_DIR / IMPORT_DIR_NAME  # data/import
EXPORT_DIR = IO_DIR / EXPORT_DIR_NAME  # data/export


IMPORT_VIDEO_DIR_NAME = "video_import"
REPORT_IMPORT_DIR_NAME = "report_import"

VIDEO_EXPORT_DIR_NAME = "video_export"
REPORT_EXPORT_DIR_NAME = "report_export"


IMPORT_VIDEO_DIR = IMPORT_DIR / IMPORT_VIDEO_DIR_NAME
IMPORT_REPORT_DIR = IMPORT_DIR / REPORT_IMPORT_DIR_NAME


VIDEO_EXPORT_DIR = EXPORT_DIR / VIDEO_EXPORT_DIR_NAME
REPORT_EXPORT_DIR = EXPORT_DIR / REPORT_EXPORT_DIR_NAME

# Document Dir

DOCUMENT_DIR = STORAGE_DIR / "documents"

# After initial import, files will remain here.

TRANSCODING_DIR = STORAGE_DIR / "temp"

SENSITIVE_VIDEO_DIR_NAME = "sensitive_videos"
SENSITIVE_REPORT_DIR_NAME = "sensitive_reports"
ANONYM_VIDEO_DIR_NAME = "processed_videos_final"
ANONYM_REPORT_DIR_NAME = "processed_reports_final"

RAW_FRAME_DIR_NAME = f"{PREFIX_RAW}frames"
FRAME_DIR_NAME = "frames"
WEIGHTS_DIR_NAME = "model_weights"
EXAMINATION_DIR_NAME = "examinations"

# Define data subdirectories under STORAGE_DIR
ANONYM_VIDEO_DIR = STORAGE_DIR / ANONYM_VIDEO_DIR_NAME
SENSITIVE_VIDEO_DIR = STORAGE_DIR / SENSITIVE_VIDEO_DIR_NAME
ANONYM_REPORT_DIR = STORAGE_DIR / ANONYM_REPORT_DIR_NAME
SENSITIVE_REPORT_DIR = STORAGE_DIR / SENSITIVE_REPORT_DIR_NAME

FRAME_DIR = STORAGE_DIR / FRAME_DIR_NAME


WEIGHTS_DIR = STORAGE_DIR / WEIGHTS_DIR_NAME
RAW_FRAME_DIR = STORAGE_DIR / RAW_FRAME_DIR_NAME

WEIGHTS_IMPORT_DIR = IMPORT_DIR / WEIGHTS_DIR_NAME
WEIGHTS_EXPORT_DIR = EXPORT_DIR / WEIGHTS_DIR_NAME

FRAME_IMPORT_DIR = IMPORT_DIR / FRAME_DIR_NAME

FRAME_EXPORT_DIR = EXPORT_DIR / FRAME_DIR_NAME


data_paths: Dict[str, Path] = {
    "storage": STORAGE_DIR,
    "import": IMPORT_DIR,
    "import_video": IMPORT_VIDEO_DIR,
    "sensitive_video": SENSITIVE_VIDEO_DIR,
    "sensitive_report": SENSITIVE_REPORT_DIR,
    "anonym_video": ANONYM_VIDEO_DIR,
    "anonym_report": ANONYM_REPORT_DIR,
    "import_frame": FRAME_IMPORT_DIR,
    "import_report": IMPORT_REPORT_DIR,
    "raw_frame": RAW_FRAME_DIR,
    "weights": WEIGHTS_DIR,
    "weights_import": WEIGHTS_IMPORT_DIR,
    "export": EXPORT_DIR,
    "report_export": REPORT_EXPORT_DIR,
    "video_export": VIDEO_EXPORT_DIR,
    "frame_export": FRAME_EXPORT_DIR,
    "weights_export": EXPORT_DIR / WEIGHTS_DIR_NAME,
    "transcoding": TRANSCODING_DIR,
    "frame": FRAME_DIR,
    "documents": DOCUMENT_DIR,
}

logger.info(f"Storage directory: {STORAGE_DIR.resolve()}")
logger.info(f"Export directory: {EXPORT_DIR.resolve()}")

for key, path in data_paths.items():
    path.mkdir(parents=True, exist_ok=True)

    logger.info(f"{key.capitalize()} directory: {path.resolve()}")


def to_storage_relative(path: Union[str, Path]) -> str:
    """
    Return a path string relative to STORAGE_DIR, suitable for Django FileField.name.

    - If `path` is inside STORAGE_DIR (absolute or contains STORAGE_DIR as prefix),
      we strip the STORAGE_DIR prefix and return the relative part.
    - If `path` is outside STORAGE_DIR, we return it as a string unchanged
      (caller can decide what to do).
    """
    # Normalize input to Path
    p = Path(path)

    # Resolve absolute path for comparison
    storage_root = STORAGE_DIR.resolve()

    if not p.is_absolute():
        # Resolve relative path against current working directory
        # (in tests, cwd should be project root, so this still lands under STORAGE_DIR)
        p = p.resolve()

    try:
        rel = p.relative_to(storage_root)
    except ValueError:
        # Not under STORAGE_DIR – probably already a relative name or external.
        # In that case, just return the string representation as-is.
        return str(path)

    # Use POSIX-style separators for Django FileField
    return rel.as_posix()
