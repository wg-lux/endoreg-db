
from pathlib import Path
import os

from base_settings import (
    INSTALLED_APPS,
    DEFAULT_AUTO_FIELD,
    TIME_ZONE,
    STATIC_URL,
    STATIC_ROOT,
    MEDIA_ROOT,
    MEDIA_URL,
    BASE_DIR,
    TEMPLATES,
    ROOT_URLCONF,
    get_logging_config,
    MIDDLEWARE,

)
# Only keep settings that are different from base_settings.py


ASSET_DIR = Path(__file__).parent / "assets"
RUN_VIDEO_TESTS = os.environ.get("RUN_VIDEO_TESTS", "true").lower() == "true"

DEBUG = True
SECRET_KEY = "fake-key"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    },
}
