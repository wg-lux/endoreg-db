import os
from pathlib import Path
from icecream import ic

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
