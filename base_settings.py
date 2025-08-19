import os
from pathlib import Path

from endoreg_db.utils.paths import STORAGE_DIR
# from lx_annotate.settings.prod import X_FRAME_OPTIONS
X_FRAME_OPTIONS = "SAMEORIGIN"

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")

# Shared settings for dev and test
BASE_DIR = Path(__file__).parent

MEDIA_ROOT = STORAGE_DIR
MEDIA_URL = '/media/'

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATIC_ROOT.mkdir(exist_ok = True)

FILE_LOG_LEVEL = os.environ.get("FILE_LOG_LEVEL", "DEBUG").upper()

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

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

TIME_ZONE = "Europe/Berlin"

# These are commonly used in both, but can be overridden if needed
ROOT_URLCONF = "endoreg_db.urls"

# Import paths and storage helpers for use in child settings

from endoreg_db.logger_conf import get_logging_config

LOGGER_NAMES = [
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

LOGGING = get_logging_config(LOGGER_NAMES, file_log_level=FILE_LOG_LEVEL)

# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# SECURE_HSTS_SECONDS = 3600
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True
# SECURE_BROWSER_XSS_FILTER = True
# SECURE_CONTENT_TYPE_NOSNIFF = True



REST_FRAMEWORK = {
    # Keep any existing settings you already have here...

    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",      # per authenticated user
        "rest_framework.throttling.AnonRateThrottle",      # per IP for anonymous
        "rest_framework.throttling.ScopedRateThrottle",    # per-view scopes
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Adjust to your traffic/profile; these are examples
        "user": "100/hour",
        "anon": "20/hour",

        # Our new endpoint-specific scope
        "secure-url-validate": "10/min",
    },
}
