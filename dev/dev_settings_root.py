"""
Development-settings clone that uses the repository root as BASE_DIR.

Location suggested: /home/admin/dev/lx-annotate/dev_settings_root.py
"""

from pathlib import Path

from endoreg_db.utils.paths import STORAGE_DIR
from endoreg_db.logger_conf import get_logging_config

# ------------------------------------------------------------------------------
# Core settings
# ------------------------------------------------------------------------------
DEBUG = True
SECRET_KEY = "fake-key"
USE_TZ = True
INSTALLED_APPS = [
    "tests",
    "endoreg_db.apps.EndoregDbConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ------------------------------------------------------------------------------    
# Paths
# ------------------------------------------------------------------------------
# Top-level directory of the mono-repo (…/lx-annotate)
BASE_DIR = Path(__file__).resolve().parents[2]   # go two levels up **from this file**

# SQLite sits next to manage.py so both the Vue app and any sub-packages
# see the *same* database.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "dev_db.sqlite3",
    },
}

TIME_ZONE = "Europe/Berlin"

# ------------------------------------------------------------------------------
# Media & static
# ------------------------------------------------------------------------------
MEDIA_ROOT = STORAGE_DIR           # already absolute
MEDIA_URL = "/media/"

STATIC_URL = "/static/"

# ------------------------------------------------------------------------------
# Logging (unchanged)
# ------------------------------------------------------------------------------
TEST_LOGGER_NAMES = [
    "tests",
    "paths",
    "raw_pdf",
    "patient",
    "default_objects",
    "ffmpeg_wrapper",
    # Video-pipeline modules
    "endoreg_db.models.media.video.video_file",
    "endoreg_db.models.media.video.video_file_anonymize",
    "endoreg_db.models.media.video.pipe_1",
    "endoreg_db.models.media.video.pipe_2",
    "endoreg_db.utils.pipelines.process_video_dir",
    "endoreg_db.models.metadata.sensitive_meta",
]

LOGGING = get_logging_config(
    TEST_LOGGER_NAMES,
    file_log_level="INFO",
    console_log_level="WARNING",
)

# ------------------------------------------------------------------------------
# URLs / CORS
# ------------------------------------------------------------------------------
ROOT_URLCONF = "endoreg_db.urls"

CORS_ALLOWED_ORIGINS = [
    "http://127.0.0.1:5174",
    "http://127.0.0.1:8000",
]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:5174",
    # "http://127.0.0.1:5174/api/patients", # AI Suggestion: The CSRF_TRUSTED_ORIGINS should only contain origins (protocol + domain + port), not paths. The second entry with /api/patients path is incorrect.
]

# ------------------------------------------------------------------------------
# Templates (unchanged)
# ------------------------------------------------------------------------------
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
