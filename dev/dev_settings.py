from pathlib import Path
from django.conf.global_settings import STATIC_URL
from icecream import ic
from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.logger_conf import get_logging_config

DEBUG = True
SECRET_KEY = "fake-key"
INSTALLED_APPS = [
    "tests",
    "endoreg_db.apps.EndoregDbConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BASE_DIR = Path(__file__).parent.parent
ic(f"endoreg_db.dev.dev_settings.py - BASE_DIR: {BASE_DIR}")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "dev_db.sqlite3",
    },
}

TIME_ZONE = "Europe/Berlin"

MEDIA_ROOT = STORAGE_DIR
MEDIA_URL = '/media/' # Adjust if needed

# --- Define logger names needed for tests ---
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
    "endoreg_db.utils.pipelines.process_video_dir"
    "endoreg_db.models.metadata.sensitive_meta"
    # Add any other specific loggers used in your tests or app code
]

# --- Use the imported function to generate LOGGING ---
LOGGING = get_logging_config(TEST_LOGGER_NAMES, file_log_level="INFO",
                             console_log_level = "WARNING") # Or set level via env var

STATIC_URL = "static/"

ROOT_URLCONF = "endoreg_db.urls"
CORS_ALLOWED_ORIGINS = ["http://127.0.0.1:5174", "http://127.0.0.1:8000"]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5174/api/patients",
]
