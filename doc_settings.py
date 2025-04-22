import os
from pathlib import Path
from icecream import ic


ic(f"LOADING SETTINGS: {__file__}")

DEBUG=True
SECRET_KEY = "fake-key"
INSTALLED_APPS = [
    "tests",
    "endoreg_db.apps.EndoregDbConfig",
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
]
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

BASE_DIR = Path(__file__).parent
print(f"doc_settings.py - BASE_DIR: {BASE_DIR}")

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': str(BASE_DIR / 'docs_db.sqlite3'),
    },
}

TIME_ZONE = "Europe/Berlin"
