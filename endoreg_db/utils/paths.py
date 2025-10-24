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

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "storage"))

storage_dir_env = os.getenv("STORAGE_DIR")
if storage_dir_env is None:
    raise RuntimeError("STORAGE_DIR environment variable is not set.")
storage_dir_str = Path(storage_dir_env)
STORAGE_DIR = storage_dir_str
if not STORAGE_DIR.exists():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)

PREFIX_RAW = "raw_"
IMPORT_DIR_NAME = "import"
EXPORT_DIR_NAME = "export"

VIDEO_DIR_NAME = "videos"
ANONYM_VIDEO_DIR_NAME = "anonym_videos" # Added for processed videos
FRAME_DIR_NAME = "frames"
PDF_DIR_NAME = "pdfs" # Changed from reports
WEIGHTS_DIR_NAME = "model_weights"
EXAMINATION_DIR_NAME = "examinations"

RAW_VIDEO_DIR_NAME = f"{PREFIX_RAW}videos"
RAW_FRAME_DIR_NAME = f"{PREFIX_RAW}frames"
RAW_PDF_DIR_NAME = f"{PREFIX_RAW}pdfs" # Changed from reports

# Define data subdirectories under STORAGE_DIR
VIDEO_DIR = STORAGE_DIR / VIDEO_DIR_NAME
ANONYM_VIDEO_DIR = STORAGE_DIR / ANONYM_VIDEO_DIR_NAME # Added
FRAME_DIR = STORAGE_DIR / FRAME_DIR_NAME
PDF_DIR = STORAGE_DIR / PDF_DIR_NAME # Changed
WEIGHTS_DIR = STORAGE_DIR / WEIGHTS_DIR_NAME
RAW_VIDEO_DIR = STORAGE_DIR / RAW_VIDEO_DIR_NAME
RAW_FRAME_DIR = STORAGE_DIR / RAW_FRAME_DIR_NAME
RAW_PDF_DIR = STORAGE_DIR / RAW_PDF_DIR_NAME # Changed

IMPORT_DIR = STORAGE_DIR / IMPORT_DIR_NAME
VIDEO_IMPORT_DIR = IMPORT_DIR / VIDEO_DIR_NAME
FRAME_IMPORT_DIR = IMPORT_DIR / FRAME_DIR_NAME
PDF_IMPORT_DIR = IMPORT_DIR / PDF_DIR_NAME # Changed
WEIGHTS_IMPORT_DIR = IMPORT_DIR / WEIGHTS_DIR_NAME

EXPORT_DIR = STORAGE_DIR / EXPORT_DIR_NAME

data_paths:Dict[str,Path] = {
    "storage": STORAGE_DIR,
    "video": VIDEO_DIR,
    "anonym_video": ANONYM_VIDEO_DIR, # Added
    "frame": FRAME_DIR,
    "pdf": PDF_DIR, # Changed
    "import": IMPORT_DIR,
    "video_import": VIDEO_IMPORT_DIR,
    "frame_import": FRAME_IMPORT_DIR,
    "pdf_import": PDF_IMPORT_DIR, # Changed
    "raw_video": RAW_VIDEO_DIR,
    "raw_frame": RAW_FRAME_DIR,
    "raw_pdf": RAW_PDF_DIR, # Changed
    "weights": WEIGHTS_DIR,
    "weights_import": WEIGHTS_IMPORT_DIR,
    "export": EXPORT_DIR,
    "video_export": EXPORT_DIR / VIDEO_DIR_NAME,
    "anonym_video_export": EXPORT_DIR / ANONYM_VIDEO_DIR_NAME, # Added
    "frame_export": EXPORT_DIR / FRAME_DIR_NAME,
    "pdf_export": EXPORT_DIR / PDF_DIR_NAME, # Changed
    "weights_export": EXPORT_DIR / WEIGHTS_DIR_NAME,
    "examination_export": EXPORT_DIR / EXAMINATION_DIR_NAME,
    "raw_video_export": EXPORT_DIR / RAW_VIDEO_DIR_NAME,
    "raw_frame_export": EXPORT_DIR / RAW_FRAME_DIR_NAME,
    "raw_pdf_export": EXPORT_DIR / RAW_PDF_DIR_NAME, # Changed
}

logger.info(f"Storage directory: {STORAGE_DIR.resolve()}")
logger.info(f"Export directory: {EXPORT_DIR.resolve()}")

# for key, path in data_paths.items():
#     path.mkdir(parents=True, exist_ok=True)

#     logger.info(f"{key.capitalize()} directory: {path.resolve()}")
