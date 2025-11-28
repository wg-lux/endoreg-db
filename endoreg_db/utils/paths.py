"""
Centralizes path management for the application.

This module sets up all necessary directories for storage and import operations.
It provides a unified dictionary 'data_paths' for accessing all path objects.
"""

from logging import getLogger

from sphinx.search import no
logger = getLogger(__name__)

from pathlib import Path
from typing import Dict
import os
# Alternative approach using env_path helper, deprecated since monorepo setup. Alright for single install, env is always preferred.
#from endoreg_db.config.env import env_path

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

IMPORT_DIR_NAME = IO_DIR / "import"
EXPORT_DIR_NAME = IO_DIR / "export"


SENSITIVE_VIDEO_DIR_NAME = "delete_videos"
SENSITIVE_REPORT_DIR_NAME = "delete_reports"
ANONYM_VIDEO_DIR_NAME = "temporary_videos" # Added for finalized videos
ANONYM_REPORT_DIR_NAME = "temporary_reports" # Added for finalized reports
IMPORT_VIDEO_DIR_NAME = "video_import"
IMPORT_REPORT_DIR_NAME = "report_import"
VIDEO_EXPORT_DIR_NAME = "video_export"
REPORT_EXPORT_DIR_NAME = "report_export"

FRAME_DIR_NAME = "frames"
WEIGHTS_DIR_NAME = "model_weights"
EXAMINATION_DIR_NAME = "examinations"

RAW_VIDEO_DIR_NAME = f"{PREFIX_RAW}videos"
RAW_REPORT_DIR_NAME = f"{PREFIX_RAW}reports"


RAW_FRAME_DIR_NAME = f"{PREFIX_RAW}frames"


# Define data subdirectories under STORAGE_DIR
ANONYM_VIDEO_DIR = STORAGE_DIR / ANONYM_VIDEO_DIR_NAME # Added
SENSITIVE_VIDEO_DIR = STORAGE_DIR /SENSITIVE_VIDEO_DIR_NAME

FRAME_DIR = STORAGE_DIR / FRAME_DIR_NAME


SENSITIVE_REPORT_DIR = STORAGE_DIR / SENSITIVE_REPORT_DIR_NAME
ANONYM_REPORT_DIR = STORAGE_DIR / ANONYM_REPORT_DIR_NAME


WEIGHTS_DIR = STORAGE_DIR / WEIGHTS_DIR_NAME
RAW_VIDEO_DIR = STORAGE_DIR / RAW_VIDEO_DIR_NAME
RAW_FRAME_DIR = STORAGE_DIR / RAW_FRAME_DIR_NAME
RAW_REPORT_DIR = STORAGE_DIR / RAW_REPORT_DIR_NAME # Changed

IMPORT_DIR = IO_DIR / IMPORT_DIR_NAME
VIDEO_IMPORT_DIR = IMPORT_DIR / IMPORT_VIDEO_DIR_NAME
FRAME_IMPORT_DIR = IMPORT_DIR / FRAME_DIR_NAME

REPORT_IMPORT_DIR = IMPORT_DIR / IMPORT_REPORT_DIR_NAME # Changed
WEIGHTS_IMPORT_DIR = IMPORT_DIR / WEIGHTS_DIR_NAME

EXPORT_DIR = IO_DIR / EXPORT_DIR_NAME
VIDEO_EXPORT_DIR = EXPORT_DIR / VIDEO_EXPORT_DIR_NAME
REPORT_EXPORT_DIR = EXPORT_DIR / REPORT_EXPORT_DIR_NAME
FRAME_EXPORT_DIR = EXPORT_DIR / FRAME_DIR_NAME

data_paths:Dict[str,Path] = {
    "storage": STORAGE_DIR,
    "anonym_video": ANONYM_VIDEO_DIR, # Added
    "frame": FRAME_DIR,
    "import": IMPORT_DIR,
    "video_import": VIDEO_IMPORT_DIR,
    "frame_import": FRAME_IMPORT_DIR,
    "report_import": REPORT_IMPORT_DIR, # Changed
    "raw_video": RAW_VIDEO_DIR,
    "raw_frame": RAW_FRAME_DIR,
    "raw_report": RAW_REPORT_DIR, # Changed
    "weights": WEIGHTS_DIR,
    "weights_import": WEIGHTS_IMPORT_DIR,
    "export": EXPORT_DIR,
    "anonym_report_export": REPORT_EXPORT_DIR, # Added
    "frame_export": FRAME_DIR,
    "report_export": REPORT_EXPORT_DIR, # Changed
    "weights_export": EXPORT_DIR / WEIGHTS_DIR_NAME,
}

logger.info(f"Storage directory: {STORAGE_DIR.resolve()}")
logger.info(f"Export directory: {EXPORT_DIR.resolve()}")

# for key, path in data_paths.items():
#     path.mkdir(parents=True, exist_ok=True)

#     logger.info(f"{key.capitalize()} directory: {path.resolve()}")
