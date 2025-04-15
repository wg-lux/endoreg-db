import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
# This will not override existing environment variables
load_dotenv()

def get_env_var(key):
    value = os.getenv(key)
    if value:
        return value.strip('"\'')  # Strip both single and double quotes
    return None

STORAGE_DIR_NAME = get_env_var("DJANGO_STORAGE_DIR_NAME") or "data"
STORAGE_DIR = get_env_var("DJANGO_STORAGE_DIR") or STORAGE_DIR_NAME
FRAME_DIR_NAME = get_env_var("DJANGO_FRAME_DIR_NAME") or "db_frames"
VIDEO_DIR_NAME = get_env_var("DJANGO_VIDEO_DIR_NAME") or "db_videos"
WEIGHTS_DIR_NAME = get_env_var("DJANGO_WEIGHTS_DIR_NAME") or "db_model_weights"
PDF_DIR_NAME = get_env_var("DJANGO_PDF_DIR_NAME") or "pdfs"

def get_storage_dir(raw: bool = False):
    """
    Get the storage directory from the environment variable or settings.
    """
    storage_dir = Path(STORAGE_DIR)

    if raw:
        name = storage_dir.name
        storage_dir = storage_dir.parent / f"raw_{name}"


    if not storage_dir.exists():
        storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir

def get_frame_dir(raw: bool = False):
    """
    Get the frame directory from the environment variable or settings.
    """
    frame_dir_name = os.environ.get("DJANGO_FRAME_DIR_NAME", FRAME_DIR_NAME)
    storage_dir = get_storage_dir(raw)
    frame_dir = storage_dir / frame_dir_name
    if not frame_dir.exists():
        frame_dir.mkdir(parents=True, exist_ok=True)
    return frame_dir

def get_video_dir(raw: bool = False):
    """
    Get the video directory from the environment variable or settings.
    """
    video_dir_name = os.environ.get("DJANGO_VIDEO_DIR_NAME", VIDEO_DIR_NAME)
    storage_dir = get_storage_dir(raw)
    video_dir = storage_dir / video_dir_name
    if not video_dir.exists():
        video_dir.mkdir(parents=True, exist_ok=True)
    return video_dir

def get_weights_dir():
    """
    Get the weights directory from the environment variable or settings.
    """
    weights_dir_name = os.environ.get("DJANGO_WEIGHTS_DIR_NAME", WEIGHTS_DIR_NAME)
    storage_dir = get_storage_dir()
    weights_dir = storage_dir / weights_dir_name
    if not weights_dir.exists():
        weights_dir.mkdir(parents=True, exist_ok=True)
    return weights_dir

def get_pdf_dir(raw: bool = False):
    """
    Get the pdf directory from the environment variable or settings.
    """
    pdf_dir_name = os.environ.get("DJANGO_PDF_DIR_NAME", PDF_DIR_NAME)
    storage_dir = get_storage_dir(raw)
    pdf_dir = storage_dir / pdf_dir_name
    if not pdf_dir.exists():
        pdf_dir.mkdir(parents=True, exist_ok=True)
    return pdf_dir

STORAGE_DIR = get_storage_dir()
FRAME_DIR = get_frame_dir()
VIDEO_DIR = get_video_dir()
PDF_DIR = get_pdf_dir()
PDF_DIR_NAME = PDF_DIR.name
RAW_VIDEO_DIR = get_video_dir(raw=True)
RAW_VIDEO_DIR_NAME = RAW_VIDEO_DIR.name
RAW_FRAME_DIR = get_frame_dir(raw=True)
RAW_FRAME_DIR_NAME = RAW_FRAME_DIR.name
RAW_PDF_DIR = get_pdf_dir(raw=True)
RAW_PDF_DIR_NAME = RAW_PDF_DIR.name
WEIGHTS_DIR = get_weights_dir()
TEST_RUN = os.environ.get("TEST_RUN", False)
TEST_RUN_FRAME_NUMBER = os.environ.get("TEST_RUN_FRAME_NUMBER", 1000)
# AI Stuff
FRAME_PROCESSING_BATCH_SIZE = os.environ.get("DJANGO_FRAME_PROCESSING_BATCH_SIZE", 10)

