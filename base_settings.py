from pathlib import Path
import os
from endoreg_db.utils.paths import STORAGE_DIR

# Shared settings for dev and test
BASE_DIR = Path(__file__).parent

MEDIA_ROOT = STORAGE_DIR
MEDIA_URL = '/media/'

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATIC_ROOT.mkdir(exist_ok = True)

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

STATIC_URL = "/static/"

# These are commonly used in both, but can be overridden if needed
ROOT_URLCONF = "endoreg_db.urls"

# Import paths and storage helpers for use in child settings

from endoreg_db.logger_conf import get_logging_config
