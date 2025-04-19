"""
Centralizes path management for the application.

This module sets up all necessary directories for storage and import operations.
It provides a unified dictionary 'data_paths' for accessing all path objects.
"""

import os
from pathlib import Path
from typing import Dict
import dotenv

dotenv.load_dotenv()

from icecream import ic
debug = os.getenv("DEBUG", "false").lower() == "true"

BASE_DIR = os.getcwd()

PREFIX_RAW = "raw_"
STORAGE_DIR_NAME = "data"
IMPORT_DIR_NAME = "import"
EXPORT_DIR_NAME = "export"

VIDEO_DIR_NAME = "videos"
FRAME_DIR_NAME = "frames"
REPORT_DIR_NAME = "reports"
WEIGHTS_DIR_NAME = "model_weights"
EXAMINATION_DIR_NAME = "examinations"

RAW_VIDEO_DIR_NAME = f"{PREFIX_RAW}videos"
RAW_FRAME_DIR_NAME = f"{PREFIX_RAW}frames"
RAW_REPORT_DIR_NAME = f"{PREFIX_RAW}reports"


STORAGE_DIR = Path(STORAGE_DIR_NAME)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR = STORAGE_DIR / VIDEO_DIR_NAME
FRAME_DIR = STORAGE_DIR / FRAME_DIR_NAME
REPORT_DIR = STORAGE_DIR / REPORT_DIR_NAME
WEIGHTS_DIR = STORAGE_DIR / WEIGHTS_DIR_NAME
RAW_VIDEO_DIR = STORAGE_DIR / RAW_VIDEO_DIR_NAME
RAW_FRAME_DIR = STORAGE_DIR / RAW_FRAME_DIR_NAME
RAW_REPORT_DIR = STORAGE_DIR / RAW_REPORT_DIR_NAME

IMPORT_DIR = STORAGE_DIR / IMPORT_DIR_NAME
VIDEO_IMPORT_DIR = IMPORT_DIR / VIDEO_DIR_NAME
FRAME_IMPORT_DIR = IMPORT_DIR / FRAME_DIR_NAME
REPORT_IMPORT_DIR = IMPORT_DIR / REPORT_DIR_NAME
WEIGHTS_IMPORT_DIR = IMPORT_DIR / WEIGHTS_DIR_NAME

EXPORT_DIR = STORAGE_DIR / EXPORT_DIR_NAME

data_paths:Dict[str,Path] = {
    "storage": STORAGE_DIR,
    "video": VIDEO_DIR,
    "frame": FRAME_DIR,
    "report": REPORT_DIR,
    "import": IMPORT_DIR,
    "video_import": VIDEO_IMPORT_DIR,
    "frame_import": FRAME_IMPORT_DIR,
    "report_import": REPORT_IMPORT_DIR,
    "raw_video": RAW_VIDEO_DIR,
    "raw_frame": RAW_FRAME_DIR,
    "raw_report": RAW_REPORT_DIR,
    "weights": WEIGHTS_DIR,
    "weights_import": WEIGHTS_IMPORT_DIR,
    "export": EXPORT_DIR,
    "video_export": EXPORT_DIR / VIDEO_DIR_NAME,
    "frame_export": EXPORT_DIR / FRAME_DIR_NAME,
    "report_export": EXPORT_DIR / REPORT_DIR_NAME,
    "weights_export": EXPORT_DIR / WEIGHTS_DIR_NAME,
    "examination_export": EXPORT_DIR / EXAMINATION_DIR_NAME,
    "raw_video_export": EXPORT_DIR / RAW_VIDEO_DIR_NAME,
    "raw_frame_export": EXPORT_DIR / RAW_FRAME_DIR_NAME,
}

for key, path in data_paths.items():
    path.mkdir(parents=True, exist_ok=True)
    if debug:
        print(f"{key.capitalize()} directory: {path}")

if debug:
    ic("Backend storage paths:")
    for key, path in data_paths.items():
        ic(f"{key.capitalize()} directory: {path}")
