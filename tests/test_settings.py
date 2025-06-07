from base_settings import *
from pathlib import Path
import os

# Only keep settings that are different from base_settings.py


ASSET_DIR = Path(__file__).parent / "assets"
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"

DEBUG = True
SECRET_KEY = "fake-key"

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    },
}



MIDDLEWARE = [
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


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
    # Add any other specific loggers used in your tests or app code
]

LOGGING = get_logging_config(TEST_LOGGER_NAMES, file_log_level="INFO")
