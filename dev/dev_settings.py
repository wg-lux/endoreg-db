from config.settings.dev import *  # noqa

from pathlib import Path
from icecream import ic
import os

# Define BASE_DIR first
BASE_DIR = Path(__file__).parent.parent

# Set STORAGE_DIR environment variable if not set
if not os.environ.get("STORAGE_DIR"):
    os.environ["STORAGE_DIR"] = str(BASE_DIR / "storage")

DEBUG = True
SECRET_KEY = "fake-key"

# Add the missing RUN_VIDEO_TESTS setting
RUN_VIDEO_TESTS = True

# Add the missing ASSET_DIR setting for test video files
ASSET_DIR = BASE_DIR / "data"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "dev_db.sqlite3",
    },
}

MEDIA_ROOT = STORAGE_DIR
MEDIA_URL = '/media/' # Adjust if needed

# --- Define logger names needed for dev ---
TEST_LOGGER_NAMES = [
    "tests", # General test logger
    "paths",
    "raw_pdf",
    "patient",
    "default_objects",
    # "video_file", # Removed generic logger
    "ffmpeg_wrapper",
    # Add specific loggers based on __name__
    "endoreg_db.models.media.video.video_file",
    "endoreg_db.models.media.video.video_file_anonymize",
    "endoreg_db.models.media.video.pipe_1",
    "endoreg_db.models.media.video.pipe_2",
    "endoreg_db.utils.pipelines.process_video_dir",
    "endoreg_db.models.metadata.sensitive_meta",
    # Add any other specific loggers used in your tests or app code
]

# --- Use the imported function to generate LOGGING ---
LOGGING = get_logging_config(TEST_LOGGER_NAMES, file_log_level="INFO",
                             console_log_level = "WARNING") # Or set level via env var

STATIC_URL = "/static/"  # Uncommented - required by staticfiles app

ROOT_URLCONF = "endoreg_db.urls"
CORS_ALLOWED_ORIGINS = ["http://127.0.0.1:5174", "http://127.0.0.1:8000"]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5174/patients",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]